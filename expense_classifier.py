"""
Expense Classifier — business vs. personal spending.

Answers the auditor's core question: is this transaction a legitimate
business expense or not? Three classification layers, cheapest first:

  1. Deterministic rules — internal transfers, payroll, and the
     per-client learned vendor lists (populated by the confirmation
     pass). No judgment calls, high confidence.
  2. Heuristics — keyword signals against the transaction's category
     and vendor. Medium confidence: the same merchant can flip meaning
     between businesses (a gym membership is personal for a fleet
     company, business for a fitness studio), so heuristics never
     claim certainty.
  3. LLM review — the ambiguous remainder, batched by vendor with the
     client's business profile as context. Budget-capped; skipped
     entirely when no API key is set.

Every event gets:
    expense_class       business | personal | transfer | needs_review
    expense_confidence  high | medium | low
    expense_reason      one-line human-readable why

The confirmation pass (CONFIRM / DISMISS per finding) feeds the learned
vendor lists, so audit #2 on the same client classifies more at high
confidence and the needs_review queue shrinks over time.
"""

import json
import os
import re

import pandas as pd

from financial_normalizer import clean_vendor

# Classes
BUSINESS = "business"
PERSONAL = "personal"
TRANSFER = "transfer"
NEEDS_REVIEW = "needs_review"

# Vendors are batched into one LLM prompt; keeps the per-run call count
# (and cost) bounded regardless of ledger size.
_VENDORS_PER_PROMPT = 12

# ---------------------------------------------------------------------------
# Heuristic keyword signals
# ---------------------------------------------------------------------------
# These are *signals*, not verdicts — medium confidence at best. The
# business profile and the learned lists outrank them.

_BUSINESS_KEYWORDS = [
    # fleet / vehicle operations
    "fuel", "gas station", "shell", "chevron", "exxon", "bp ", "tires",
    "maintenance", "repair", "auto parts", "oil change", "toll", "parking",
    # running the business
    "insurance", "rent", "utilities", "electric", "phone", "internet",
    "software", "saas", "cloud", "subscription", "license",
    "payroll", "staffing", "contractor", "legal", "accounting", "audit",
    "consulting", "marketing", "advertising",
]

_PERSONAL_KEYWORDS = [
    # media & entertainment
    "netflix", "hulu", "disney", "spotify", "entertainment", "cinema",
    "gaming", "xbox", "playstation",
    # health / lifestyle
    "gym", "fitness", "planet fitness",
    # food away from home (ambiguous for some businesses — medium only)
    "mcdonald", "fast food", "doordash", "ubereats", "grubhub",
    # family
    "kids", "daycare", "toys",
]


def _keyword_signal(text: str):
    """Return (class, reason) from keyword signals, or None."""
    t = f" {text.lower()} "
    for kw in _PERSONAL_KEYWORDS:
        if kw in t:
            return PERSONAL, f"matches personal-spend signal '{kw.strip()}'"
    for kw in _BUSINESS_KEYWORDS:
        if kw in t:
            return BUSINESS, f"matches business-spend signal '{kw.strip()}'"
    return None


# ---------------------------------------------------------------------------
# Layer 1 — deterministic rules
# ---------------------------------------------------------------------------

def _learned_lookup(vendor: str, cfg: dict):
    """Check the per-client learned vendor lists (cleaned for matching)."""
    v = clean_vendor(vendor)
    for known in cfg.get("confirmed_business_vendors") or []:
        if clean_vendor(known) == v:
            return BUSINESS, "confirmed as business in a prior audit"
    for known in cfg.get("confirmed_personal_vendors") or []:
        if clean_vendor(known) == v:
            return PERSONAL, "confirmed as personal in a prior audit"
    return None


def _rule_classify(row, cfg: dict):
    """Deterministic layer. Returns (class, confidence, reason) or None."""
    transfer_cats = {c.lower() for c in
                     cfg.get("transfer_categories") or []}
    if str(row.get("category", "")).lower() in transfer_cats:
        return TRANSFER, "high", "internal money movement, not spending"

    if row.get("source") == "payroll" or str(
            row.get("category", "")).lower() == "payroll":
        return BUSINESS, "high", "payroll is a business expense by definition"

    learned = _learned_lookup(row.get("vendor_payee", ""), cfg)
    if learned:
        cls, reason = learned
        return cls, "high", reason
    return None


# ---------------------------------------------------------------------------
# Layer 2 — heuristics
# ---------------------------------------------------------------------------

def _heuristic_classify(row, cfg: dict):
    """Keyword layer. Returns (class, confidence, reason) or None."""
    # The auditor's own auxiliary lens outranks generic keywords.
    aux_cats = {c.lower() for c in cfg.get("auxiliary_categories") or []}
    if str(row.get("category", "")).lower() in aux_cats:
        return (PERSONAL, "medium",
                f"category '{row.get('category')}' is flagged auxiliary "
                f"for this client")

    text = f"{row.get('vendor_raw', '')} {row.get('vendor_payee', '')} " \
           f"{row.get('category', '')}"
    signal = _keyword_signal(text)
    if signal:
        cls, reason = signal
        return cls, "medium", reason
    return None


# ---------------------------------------------------------------------------
# Layer 3 — LLM review of the ambiguous remainder
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
You are an auditor classifying business expenses. For each vendor below,
decide whether its charges are legitimate BUSINESS expenses or PERSONAL
(non-business) spending, given the client's business.

Business profile:
- Industry: {industry}
- Description: {description}

Vendors (JSON array of {{"vendor": name, "category": cat, "transactions": n,
"total": amount}}):
{vendors_json}

Rules:
- Judge by whether the spend plausibly serves THIS business, not by the
  merchant alone. Fuel is business for a fleet, personal for a consultant.
- "personal" means the owner/family benefit, not the business.
- When genuinely ambiguous, choose "needs_review" — do not guess.
- confidence: "high" only when the call is obvious; otherwise "medium".

Reply with a JSON array only, one object per vendor:
[{{"vendor": "<exact name as given>", "class": "business|personal|needs_review",
   "confidence": "high|medium", "reason": "<one short sentence>"}}]
"""


def _business_profile(cfg: dict) -> dict:
    prof = cfg.get("business_profile") or {}
    return {
        "industry": prof.get("industry") or "unspecified",
        "description": prof.get("description") or "unspecified",
    }


def _classify_vendors_llm(vendors: list[dict], cfg: dict) -> dict:
    """Classify one batch of vendors via the Anthropic API.

    Returns {vendor_name: (class, confidence, reason)}. Any failure —
    no key, API error, bad JSON — returns {} so callers fall through
    to needs_review instead of crashing the pipeline.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    try:
        from anthropic import Anthropic
        prof = _business_profile(cfg)
        prompt = _CLASSIFY_PROMPT.format(
            industry=prof["industry"][:200],
            description=prof["description"][:500],
            vendors_json=json.dumps(vendors[:_VENDORS_PER_PROMPT])[:12000],
        )
        resp = Anthropic().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Tolerate code-fenced replies.
        text = re.sub(r"^```(?:json)?|```$", "", text).strip()
        parsed = json.loads(text)
    except Exception as exc:  # noqa: BLE001 — LLM is best-effort
        print(f"[classifier] LLM batch failed ({exc}); leaving needs_review.")
        return {}
    out = {}
    for item in parsed if isinstance(parsed, list) else []:
        try:
            name = str(item["vendor"])
            cls = str(item["class"]).lower()
            if cls not in (BUSINESS, PERSONAL, NEEDS_REVIEW):
                continue
            conf = str(item.get("confidence", "medium")).lower()
            conf = conf if conf in ("high", "medium") else "medium"
            out[name] = (cls, conf, str(item.get("reason", ""))[:200])
        except (KeyError, TypeError, AttributeError):
            continue
    return out


def _llm_classify(events: pd.DataFrame, cfg: dict,
                  max_calls: int) -> tuple[dict, int]:
    """Run the LLM over ambiguous vendors, batched. Returns a
    ({cleaned_vendor: (class, confidence, reason)}, calls_used) tuple."""
    pending = events[events["expense_class"] == NEEDS_REVIEW]
    if pending.empty or max_calls <= 0:
        return {}, 0
    # One decision per vendor, not per transaction — the ledger's size
    # must not dictate the API bill.
    grouped = (pending.groupby("vendor_payee")
               .agg(transactions=("amount", "size"),
                    total=("amount", "sum"),
                    category=("category", "first"),
                    vendor_raw=("vendor_raw", "first"))
               .reset_index())
    vendors = [{"vendor": r["vendor_payee"],
                "category": str(r["category"]),
                "transactions": int(r["transactions"]),
                "total": round(float(r["total"]), 2)}
               for _, r in grouped.iterrows()]
    results, calls = {}, 0
    for i in range(0, len(vendors), _VENDORS_PER_PROMPT):
        if calls >= max_calls:
            print(f"[classifier] LLM budget exhausted ({max_calls} calls); "
                  f"{len(vendors) - i} vendors left as needs_review.")
            break
        batch = _classify_vendors_llm(vendors[i:i + _VENDORS_PER_PROMPT], cfg)
        calls += 1
        for name, verdict in batch.items():
            results[clean_vendor(name)] = verdict
    print(f"[classifier] LLM reviewed {len(results)} vendors "
          f"in {calls} call(s).")
    return results, calls


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# Set to the number of LLM calls the most recent classify_expenses made
# (for shared budget accounting, mirroring sop_parser.last_used_llm).
last_llm_calls = 0


def classify_expenses(events: pd.DataFrame, cfg: dict,
                      allow_llm: bool = True) -> pd.DataFrame:
    """Add expense_class / expense_confidence / expense_reason columns.

    Layers run cheapest-first; each row takes the first layer that
    produces a verdict. Rows no layer can decide stay needs_review at
    low confidence — the confirmation queue, not a guess.
    """
    global last_llm_calls
    last_llm_calls = 0
    events = events.copy()
    events["expense_class"] = NEEDS_REVIEW
    events["expense_confidence"] = "low"
    events["expense_reason"] = "no layer produced a verdict"

    for idx, row in events.iterrows():
        verdict = _rule_classify(row, cfg) or _heuristic_classify(row, cfg)
        if verdict:
            cls, conf, reason = verdict
            events.at[idx, "expense_class"] = cls
            events.at[idx, "expense_confidence"] = conf
            events.at[idx, "expense_reason"] = reason

    if allow_llm:
        budget = cfg.get("classification_llm_budget", 10)
        llm_verdicts, last_llm_calls = _llm_classify(
            events, cfg, max_calls=budget)
        for idx, row in events[events["expense_class"] ==
                               NEEDS_REVIEW].iterrows():
            hit = llm_verdicts.get(row["vendor_payee"])
            if hit:
                cls, conf, reason = hit
                events.at[idx, "expense_class"] = cls
                events.at[idx, "expense_confidence"] = conf
                events.at[idx, "expense_reason"] = reason or \
                    "LLM classification against business profile"

    n = len(events)
    decided = int((events["expense_class"] != NEEDS_REVIEW).sum())
    print(f"[classifier] {decided}/{n} events classified; "
          f"{n - decided} need review.")
    return events


def expense_breakdown(events: pd.DataFrame) -> dict:
    """Totals by expense class for the report summary. Empty dict when
    the events were never classified."""
    if "expense_class" not in events.columns:
        return {}
    grp = events.groupby("expense_class")["amount"].agg(["sum", "size"])
    return {
        str(cls): {"total": round(float(r["sum"]), 2),
                   "transactions": int(r["size"])}
        for cls, r in grp.iterrows()
    }
