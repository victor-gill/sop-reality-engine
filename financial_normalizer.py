"""
Financial Normalizer — Step 1B of the pipeline.

Reads a bank/ledger CSV and normalizes every row into the standard
operational event log:

    timestamp    parsed datetime of the transaction
    vendor_payee cleaned counterparty name
    amount       transaction value (positive = outflow)
    category     classified spending category
    frequency    recurring interval for the vendor (or "ad-hoc")

Recurring detection: a vendor with >= 3 transactions and a stable median
interval is tagged with its cadence (weekly, bi-weekly, monthly, ...).
"""

import re

import pandas as pd

# ---------------------------------------------------------------------------
# Vendor cleaning
# ---------------------------------------------------------------------------

_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|corp|co|company|services|solutions|systems)\.?$", re.I)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")


def clean_vendor(raw: str) -> str:
    """Normalize a payee name for matching ('Twilio Inc.' -> 'twilio')."""
    name = str(raw).strip().lower()
    name = _NON_ALNUM_RE.sub(" ", name)
    name = _SUFFIX_RE.sub("", name).strip()
    return re.sub(r"\s+", " ", name)


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS = {
    "Software/SaaS": ["software", "saas", "cloud", "api", "twilio", "adobe",
                      "ledgible", "datahaven", "subscription", "license"],
    "Contractor": ["staffing", "contractor", "northbridge", "payroll",
                   "freelance"],
    "Professional Services": ["audit", "safecert", "legal", "accounting",
                              "consulting"],
    "Vendor Payout": ["invoice", "vendor", "supply", "materials"],
}


def categorize(vendor: str, memo: str = "") -> str:
    text = f"{vendor} {memo}".lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(k in text for k in keywords):
            return category
    return "Uncategorized"


# ---------------------------------------------------------------------------
# Frequency detection
# ---------------------------------------------------------------------------

def _cadence_label(median_days: float) -> str:
    if median_days <= 2:
        return "daily"
    if median_days <= 10:
        return "weekly"
    if median_days <= 20:
        return "bi-weekly"
    if median_days <= 45:
        return "monthly"
    if median_days <= 120:
        return "quarterly"
    return "annual"


def detect_frequencies(events: pd.DataFrame) -> pd.DataFrame:
    """Tag each vendor's transactions with a recurring cadence or 'ad-hoc'."""
    events = events.copy()
    events["frequency"] = "ad-hoc"
    for vendor, group in events.groupby("vendor_payee"):
        dates = group["timestamp"].sort_values().drop_duplicates()
        if len(dates) >= 3:
            median_gap = dates.diff().dt.days.median()
            if pd.notna(median_gap) and median_gap <= 120:
                events.loc[group.index, "frequency"] = _cadence_label(median_gap)
    return events


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize_ledger(path: str) -> pd.DataFrame:
    """Read a bank CSV and return the standard event log.

    Accepts flexible column names — looks for date-like, payee-like, and
    amount-like headers (e.g. 'Date'/'Posted', 'Description'/'Payee',
    'Amount'/'Debit').
    """
    df = pd.read_csv(path)

    def find(*candidates):
        for col in df.columns:
            if col.strip().lower() in candidates:
                return col
        return None

    date_col = find("date", "posted", "transaction date", "trans date")
    payee_col = find("description", "payee", "vendor", "merchant", "counterparty")
    amount_col = find("amount", "debit", "withdrawal", "value")
    memo_col = find("memo", "notes", "reference")

    if not all([date_col, payee_col, amount_col]):
        raise ValueError(
            f"Could not identify date/payee/amount columns in {list(df.columns)}")

    events = pd.DataFrame({
        "timestamp": pd.to_datetime(df[date_col], errors="coerce"),
        "vendor_payee": df[payee_col].apply(clean_vendor),
        "vendor_raw": df[payee_col].astype(str),
        "amount": pd.to_numeric(df[amount_col], errors="coerce").abs(),
    }).dropna(subset=["timestamp", "amount"])
    events["category"] = [
        categorize(v, df[memo_col].iloc[i] if memo_col else "")
        for i, v in enumerate(events["vendor_payee"])
    ]
    events = detect_frequencies(events).sort_values("timestamp")
    print(f"[normalizer] {len(events)} events from "
          f"{events['vendor_payee'].nunique()} vendors.")
    return events.reset_index(drop=True)
