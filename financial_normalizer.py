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

def _find_col(df: pd.DataFrame, *candidates):
    for col in df.columns:
        if str(col).strip().lower() in candidates:
            return col
    return None


def detect_ledger_columns(df: pd.DataFrame) -> dict:
    """Show which CSV headers were mapped to date/payee/amount.

    Returned for the upload preview so the user can see (and trust)
    the auto-detection before the pipeline runs.
    """
    return {
        "date": _find_col(df, "date", "posted", "transaction date",
                          "trans date"),
        "payee": _find_col(df, "description", "payee", "vendor", "merchant",
                           "counterparty"),
        "amount": _find_col(df, "amount", "debit", "withdrawal", "value"),
        "memo": _find_col(df, "memo", "notes", "reference"),
    }


def normalize_ledger(path: str) -> pd.DataFrame:
    """Read a bank CSV and return the standard event log.

    Accepts flexible column names — looks for date-like, payee-like, and
    amount-like headers (e.g. 'Date'/'Posted', 'Description'/'Payee',
    'Amount'/'Debit').
    """
    df = pd.read_csv(path)

    def find(*candidates):
        return _find_col(df, *candidates)

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
    events["source"] = "bank"
    events = detect_frequencies(events).sort_values("timestamp")
    print(f"[normalizer] {len(events)} events from "
          f"{events['vendor_payee'].nunique()} vendors.")
    return events.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Payroll ledger
# ---------------------------------------------------------------------------

def normalize_payroll(path: str) -> pd.DataFrame:
    """Read a payroll CSV into the standard event log.

    Flexible headers: employee/name/payee, pay date/date, net pay/gross
    pay/amount. Every event is categorized as Payroll, source='payroll'.
    """
    df = pd.read_csv(path)

    def find(*candidates):
        for col in df.columns:
            if str(col).strip().lower() in candidates:
                return col
        return None

    who_col = find("employee", "employee name", "name", "payee", "contractor",
                   "worker", "recipient")
    date_col = find("pay date", "date", "check date", "period end",
                    "payment date")
    amount_col = find("net pay", "gross pay", "amount", "total", "pay",
                      "net amount")

    if not all([who_col, date_col, amount_col]):
        raise ValueError(
            f"Could not identify employee/date/amount columns in "
            f"{list(df.columns)}")

    events = pd.DataFrame({
        "timestamp": pd.to_datetime(df[date_col], errors="coerce"),
        "vendor_payee": df[who_col].apply(clean_vendor),
        "vendor_raw": df[who_col].astype(str),
        "amount": pd.to_numeric(df[amount_col], errors="coerce").abs(),
        "category": "Payroll",
        "source": "payroll",
        "frequency": "ad-hoc",
    }).dropna(subset=["timestamp", "amount"])
    print(f"[normalizer] {len(events)} payroll events for "
          f"{events['vendor_payee'].nunique()} payees.")
    return events.reset_index(drop=True)


# ---------------------------------------------------------------------------
# P&L summary (reference for cross-checking, not additive events)
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(
    r"(?i)^(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"[\s\-_]?(\d{2,4})?|(\d{4})[\s\-_/](\d{1,2})|Q([1-4])[\s\-_]?(\d{2,4}))$")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _parse_period(label: str):
    """'Jan 2026' / '2026-01' / 'Q1 2026' -> period-start timestamp."""
    m = _PERIOD_RE.match(str(label).strip())
    if not m:
        return None
    try:
        if m.group(1):  # month name
            year = int(m.group(2)) if m.group(2) else 2026
            year += 2000 if year < 100 else 0
            return pd.Timestamp(year, _MONTHS[m.group(1).lower()], 1)
        if m.group(3):  # YYYY-MM
            return pd.Timestamp(int(m.group(3)), int(m.group(4)), 1)
        year = int(m.group(6)) if m.group(6) else 2026
        year += 2000 if year < 100 else 0
        return pd.Timestamp(year, (int(m.group(5)) - 1) * 3 + 1, 1)
    except (ValueError, TypeError):
        return None


def normalize_pnl(path: str) -> pd.DataFrame:
    """Read a P&L export into an account x period summary table.

    Handles two shapes:
      * Transaction detail (date + amount columns) -> event-log rows.
      * Summary matrix (accounts down the left, periods across the top)
        -> melted (account, period, amount) reference table used for
        P&L-vs-bank variance checks.
    """
    df = pd.read_csv(path)

    def find(*candidates):
        for col in df.columns:
            if str(col).strip().lower() in candidates:
                return col
        return None

    # Shape 1: transaction detail.
    if find("date", "posted", "transaction date") and find(
            "amount", "debit", "value", "total"):
        events = normalize_ledger(path)
        events["source"] = "pnl"
        acct_col = find("account", "category", "account name")
        if acct_col:
            events["category"] = df[acct_col].astype(str)
        events["account"] = events["vendor_payee"]
        events["period"] = events["timestamp"].dt.to_period("M").dt.to_timestamp()
        print(f"[normalizer] P&L detail: {len(events)} events.")
        return events

    # Shape 2: summary matrix.
    cols = [str(c).strip() for c in df.columns]
    period_cols = [(c, _parse_period(c)) for c in cols[1:]]
    period_cols = [(c, p) for c, p in period_cols if p is not None]
    if not period_cols:
        raise ValueError(f"Could not parse P&L shape from columns: {cols}")
    account_col = df.columns[0]
    rows = []
    for _, r in df.iterrows():
        account_raw = str(r[account_col]).strip()
        if not account_raw or account_raw.lower() in ("total", "net income", ""):
            continue
        for col, period in period_cols:
            try:
                amount = float(str(r[col]).replace(",", "").replace("$", ""))
            except (ValueError, TypeError):
                continue
            if amount:
                rows.append({"account": clean_vendor(account_raw),
                             "account_raw": account_raw,
                             "period": period,
                             "amount": abs(amount),
                             "source": "pnl"})
    summary = pd.DataFrame(rows)
    print(f"[normalizer] P&L matrix: {len(summary)} account-period cells, "
          f"{summary['account'].nunique()} accounts.")
    return summary


# ---------------------------------------------------------------------------
# Logistics / shipments
# ---------------------------------------------------------------------------

def normalize_logistics(path: str) -> pd.DataFrame:
    """Read a logistics CSV into the standard event log.

    Another dataset, same shape: timestamped cost events. Flexible
    headers: shipment date/date, carrier/vendor, origin, destination,
    cost/freight amount. Events get category='Logistics', source='logistics',
    and route metadata is kept for step matching.
    """
    df = pd.read_csv(path)

    def find(*candidates):
        for col in df.columns:
            if str(col).strip().lower() in candidates:
                return col
        return None

    date_col = find("shipment date", "ship date", "date", "departure date",
                    "delivery date")
    carrier_col = find("carrier", "vendor", "vendor_payee", "transporter",
                       "forwarder", "3pl")
    cost_col = find("cost", "freight cost", "amount", "total", "freight",
                    "charge")
    origin_col = find("origin", "from", "pickup")
    dest_col = find("destination", "to", "dropoff", "drop-off", "delivery")

    if not all([date_col, carrier_col, cost_col]):
        raise ValueError(
            f"Could not identify date/carrier/cost columns in "
            f"{list(df.columns)}")

    events = pd.DataFrame({
        "timestamp": pd.to_datetime(df[date_col], errors="coerce"),
        "vendor_payee": df[carrier_col].apply(clean_vendor),
        "vendor_raw": df[carrier_col].astype(str),
        "amount": pd.to_numeric(df[cost_col], errors="coerce").abs(),
        "category": "Logistics",
        "source": "logistics",
        "frequency": "ad-hoc",
    }).dropna(subset=["timestamp", "amount"])
    if origin_col is not None and dest_col is not None:
        events["route"] = (df[origin_col].astype(str).str.strip() + " -> " +
                           df[dest_col].astype(str).str.strip())
    events = detect_frequencies(events)
    print(f"[normalizer] {len(events)} logistics events from "
          f"{events['vendor_payee'].nunique()} carriers.")
    return events.reset_index(drop=True)
