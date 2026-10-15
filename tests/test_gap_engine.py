"""Edge-case tests for the gap engine and normalizers.

Run:  .venv/bin/python -m pytest tests/  (or python tests/test_gap_engine.py)
Covers the trust-critical paths the review flagged: cadence inference,
column detection, and cross-document reconciliation on hostile inputs.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from financial_normalizer import (normalize_ledger, normalize_payroll,
                                  normalize_pnl, detect_ledger_columns)
from gap_engine import (analyze, find_unmapped_leakage, find_payroll_gaps,
                        find_pnl_variance)


def _csv(content: str) -> str:
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w")
    f.write(content)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------

def test_detects_unusual_headers():
    df = pd.DataFrame(columns=["Posted", "Merchant", "Withdrawal", "Notes"])
    m = detect_ledger_columns(df)
    assert m["date"] == "Posted"
    assert m["payee"] == "Merchant"
    assert m["amount"] == "Withdrawal"
    assert m["memo"] == "Notes"


def test_missing_required_column_raises_clear_error():
    path = _csv("When,Who\n2026-01-01,Acme\n")
    with pytest.raises(ValueError, match="date/payee/amount"):
        normalize_ledger(path)


def test_single_transaction_no_crash():
    path = _csv("Date,Description,Amount\n2026-01-01,Acme,100.00\n")
    events = normalize_ledger(path)
    assert len(events) == 1
    findings = find_unmapped_leakage([], events)
    assert findings == []  # one-off spend is not "recurring leakage"


def test_empty_csv_no_crash():
    path = _csv("Date,Description,Amount\n")
    events = normalize_ledger(path)
    assert len(events) == 0
    assert find_unmapped_leakage([], events) == []


# ---------------------------------------------------------------------------
# Cadence inference — the false-positive frontier
# ---------------------------------------------------------------------------

def _recurring_csv(n=6, amount=100.0, start="2026-01-01"):
    dates = pd.date_range(start, periods=n, freq="MS").strftime("%Y-%m-%d")
    rows = "\n".join(f"{d},Steady Vendor,{amount}" for d in dates)
    return _csv(f"Date,Description,Amount\n{rows}\n")


def test_monthly_vendor_flagged_as_leakage():
    events = normalize_ledger(_recurring_csv())
    findings = find_unmapped_leakage([], events)
    assert len(findings) == 1
    assert findings[0]["cadence"] == "monthly"


def test_annual_renewal_not_monthly_leakage():
    # One charge a year must not read as monthly leakage.
    path = _csv("Date,Description,Amount\n2025-06-01,Annual Sub,1200\n"
                "2026-06-01,Annual Sub,1200\n")
    events = normalize_ledger(path)
    findings = find_unmapped_leakage([], events)
    assert all(f["cadence"] != "monthly" for f in findings)


def test_sop_mapped_vendor_not_flagged():
    events = normalize_ledger(_recurring_csv())
    steps = [{"step_id": "STEP_01", "expected_vendor": "Steady Vendor",
              "expected_amount": 100.0, "frequency": "monthly",
              "approval_threshold": None, "assigned_role": "Operator"}]
    assert find_unmapped_leakage(steps, events) == []


# ---------------------------------------------------------------------------
# Payroll reconciliation
# ---------------------------------------------------------------------------

def test_payroll_gaps_both_directions():
    bank = _csv("Date,Description,Amount,Memo\n"
                "2026-01-09,Staffing Co,4000,Contractor payroll\n"
                "2026-01-23,Staffing Co,4100,Contractor payroll\n")
    pay = _csv("Contractor,Pay Date,Net Pay\n"
               "Staffing Co,2026-01-09,4000\n"
               "Ghost Worker,2026-01-23,2100\n")
    gaps = find_payroll_gaps(normalize_payroll(pay), normalize_ledger(bank))
    directions = {g["direction"] for g in gaps}
    assert "missing_payment" in directions   # Ghost Worker: no bank payment
    assert "missing_record" in directions    # 01-23 bank: no payroll record


def test_matching_payroll_no_gaps():
    bank = _csv("Date,Description,Amount,Memo\n"
                "2026-01-09,Staffing Co,4000,Contractor payroll\n")
    pay = _csv("Contractor,Pay Date,Net Pay\nStaffing Co,2026-01-09,4000\n")
    assert find_payroll_gaps(normalize_payroll(pay),
                             normalize_ledger(bank)) == []


# ---------------------------------------------------------------------------
# P&L variance
# ---------------------------------------------------------------------------

def test_pnl_variance_flags_overstatement():
    bank = _csv("Date,Description,Amount\n2026-01-05,Twilio,120\n")
    pnl = _csv("Account,Jan 2026\nTwilio,700\n")
    flags = find_pnl_variance(normalize_pnl(pnl), normalize_ledger(bank))
    assert len(flags) == 1
    assert flags[0]["variance_pct"] > 80


def test_pnl_variance_ignores_dust():
    bank = _csv("Date,Description,Amount\n2026-01-05,Twilio,120\n")
    pnl = _csv("Account,Jan 2026\nTwilio,50\n")  # under $100 floor
    assert find_pnl_variance(normalize_pnl(pnl),
                             normalize_ledger(bank)) == []


def test_full_analyze_with_no_documents():
    events = pd.DataFrame(columns=["timestamp", "vendor_payee", "amount",
                                   "category", "source", "frequency"])
    result = analyze([], events)
    assert result["summary"]["events_analyzed"] == 0
    assert result["unmapped_leakage"] == []


# ---------------------------------------------------------------------------
# Generator injection resistance — SOP text must never become code
# ---------------------------------------------------------------------------

def test_hostile_sop_cannot_inject_code(tmp_path):
    from langgraph_generator import generate_workflow
    evil_action = '''Pay """ + __import__('os').system('evil') + """ the vendor'''
    steps = [{
        "step_id": 'STEP_01"; __import__("os").system("x"); "',
        "action": evil_action,
        "expected_amount": 100.0,
        "expected_vendor": "Evil Vendor",
        "approval_threshold": "5000); __import__('os').system('y'); #",
        "frequency": "monthly",
        "assigned_role": "Operator",
    }]
    out = str(tmp_path / "workflow.py")
    generate_workflow(steps, out_path=out)
    import ast
    src = open(out).read()
    # The generated file must be valid Python.
    tree = ast.parse(src)
    # The payload survives only as inert docstring text: the schema
    # class's docstring is a single constant string containing it.
    docs = [ast.get_docstring(n) for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name.endswith("Schema")
            and n.name != "WorkflowInput"]
    assert len(docs) == 1 and "__import__('os').system('evil')" in docs[0]
    # No double-quote survives sanitization, so nothing interpolated
    # can terminate the triple-quoted docstring it sits in.
    assert '"' not in docs[0]
    # And the threshold fell back to a safe float, not injected code.
    assert "5000.0" in src
