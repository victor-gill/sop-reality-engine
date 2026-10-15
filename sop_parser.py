"""
SOP Parser — Step 1A of the pipeline.

Reads an SOP document (PDF or plain text) and converts prose into structured
workflow steps:

    {
        "step_id": "STEP_01",
        "trigger": "New Vendor Invoice Received",
        "action": "Verify invoice total against purchase order",
        "condition": "If amount > 5000, route to COO for approval",
        "assigned_role": "Accounts Payable Clerk",
        "expected_vendor": "Ledgible",
        "expected_amount": 89.0,
        "expected_interval_days": 30,
    }

Two extraction paths:
  1. LLM extraction (Anthropic API) when ANTHROPIC_API_KEY is set — handles
     messy real-world SOPs written in free prose.
  2. Rules-based extraction — parses SOPs written in the labeled format
     documented in sample_data/sample_sop.txt. No API key needed.
"""

import json
import os
import re

import pdfplumber

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_sop_text(path: str) -> str:
    """Return raw text from a .pdf, .txt, or .md file."""
    if path.lower().endswith(".pdf"):
        pages = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
        return "\n".join(pages)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Rules-based extraction (no API key required)
# ---------------------------------------------------------------------------

# Labeled lines the rules parser understands:
#   STEP 1 | Trigger: ... | Action: ... | Condition: ... | Role: ...
_LABEL_RE = re.compile(r"^(trigger|action|condition|role|owner)\s*:\s*(.+)$",
                       re.IGNORECASE)
_STEP_HEADER_RE = re.compile(r"^step\s+(\d+)\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")
_INTERVAL_RE = re.compile(
    r"\b(daily|weekly|bi-?weekly|biweekly|monthly|quarterly|annually|yearly)\b",
    re.IGNORECASE)
_INTERVAL_DAYS = {
    "daily": 1, "weekly": 7, "biweekly": 14, "bi-weekly": 14,
    "monthly": 30, "quarterly": 90, "annually": 365, "yearly": 365,
}
_THRESHOLD_RE = re.compile(
    r"(?:>|over|exceeds?|greater than)\s*\$?\s?([\d,]+)", re.IGNORECASE)


_VENDOR_VERBS = {"pay", "verify", "send", "submit", "process", "review",
                 "approve", "reconcile"}


def _extract_vendor(action: str) -> str:
    """Pull the vendor proper-noun out of an action line.

    'Pay Ledgible bookkeeping subscription' -> 'Ledgible'
    'Pay SafeCert Auditing for facility safety' -> 'SafeCert Auditing'
    """
    words = action.strip().split()
    if words and words[0].lower() in _VENDOR_VERBS:
        words = words[1:]
    name = []
    for w in words:
        stripped = w.strip(",.")
        if stripped and stripped[0].isupper():
            name.append(stripped)
        elif name:
            break
    return " ".join(name)


def _parse_block(lines: list[str], step_num: int) -> dict:
    step = {
        "step_id": f"STEP_{step_num:02d}",
        "trigger": "",
        "action": "",
        "condition": "",
        "assigned_role": "",
        "expected_vendor": "",
        "expected_amount": None,
        "expected_interval_days": None,
        "approval_threshold": None,
    }
    for line in lines:
        m = _LABEL_RE.match(line.strip())
        if not m:
            continue
        label, value = m.group(1).lower(), m.group(2).strip()
        if label == "trigger":
            step["trigger"] = value
        elif label == "action":
            step["action"] = value
            step["expected_vendor"] = _extract_vendor(value)
        elif label == "condition":
            step["condition"] = value
            amt = _AMOUNT_RE.search(value)
            if amt:
                step["expected_amount"] = float(amt.group(1).replace(",", ""))
            iv = _INTERVAL_RE.search(value)
            if iv:
                step["expected_interval_days"] = _INTERVAL_DAYS[
                    iv.group(1).lower().replace("-", "")]
            th = _THRESHOLD_RE.search(value)
            if th:
                step["approval_threshold"] = float(th.group(1).replace(",", ""))
        elif label in ("role", "owner"):
            step["assigned_role"] = value
    # Also scan trigger/action for interval hints.
    if step["expected_interval_days"] is None:
        for text in (step["trigger"], step["action"]):
            iv = _INTERVAL_RE.search(text)
            if iv:
                step["expected_interval_days"] = _INTERVAL_DAYS[
                    iv.group(1).lower().replace("-", "")]
                break
    return step


def extract_steps_rules(text: str) -> list[dict]:
    """Parse labeled SOP text into structured steps."""
    steps, current, current_num = [], [], 1
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _STEP_HEADER_RE.match(line)
        if m:
            if current:
                steps.append(_parse_block(current, current_num))
            current, current_num = [], int(m.group(1))
        else:
            current.append(line)
    if current:
        steps.append(_parse_block(current, current_num))
    # Drop blocks that produced nothing usable.
    return [s for s in steps
            if s["trigger"] or s["action"] or s["condition"]]


# ---------------------------------------------------------------------------
# LLM extraction (Anthropic) — for messy real-world SOPs
# ---------------------------------------------------------------------------

_LLM_PROMPT = """\
You convert a standard operating procedure into structured JSON.
Return ONLY a JSON array. Each element must have exactly these keys:
step_id (e.g. "STEP_01"), trigger, action, condition, assigned_role,
expected_vendor (vendor/payee name or ""), expected_amount (number or null),
expected_interval_days (number or null), approval_threshold (number or null).

SOP text:
---
{TEXT}
---
"""


def extract_steps_llm(text: str) -> list[dict]:
    """Use Claude to extract steps from free-prose SOPs."""
    from anthropic import Anthropic
    client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user",
                   "content": _LLM_PROMPT.format(TEXT=text[:12000])}],
    )
    raw = resp.content[0].text.strip()
    # Tolerate code fences.
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# Set True when the most recent parse_sop call used the LLM (for
# per-session spend caps in hosted deployments).
last_used_llm = False


def parse_sop(path: str, allow_llm: bool = True) -> list[dict]:
    """Parse an SOP file into structured steps.

    Rules-based parser runs first (free, deterministic — handles
    labeled-format SOPs). The LLM extractor is only used when the rules
    parser finds nothing AND ANTHROPIC_API_KEY is set, so free-prose
    SOPs still work without spending credits on documents the rules
    already handle. Set allow_llm=False to disable LLM use entirely
    (e.g. after a per-session cap is reached). SOPRE_FORCE_LLM=1
    forces the LLM path even when rules succeed.
    """
    global last_used_llm
    last_used_llm = False
    text = load_sop_text(path)
    steps = extract_steps_rules(text)
    if steps and not os.environ.get("SOPRE_FORCE_LLM"):
        print(f"[sop_parser] Rules-based parser extracted {len(steps)} steps.")
        return steps
    if allow_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            steps = extract_steps_llm(text)
            last_used_llm = True
            print(f"[sop_parser] LLM extracted {len(steps)} steps.")
            return steps
        except Exception as exc:  # fall through to rules on any failure
            print(f"[sop_parser] LLM extraction failed ({exc}); "
                  "falling back to rules-based parser.")
    if not steps:
        steps = extract_steps_rules(text)
    print(f"[sop_parser] Rules-based parser extracted {len(steps)} steps.")
    return steps
