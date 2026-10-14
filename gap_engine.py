"""
Gap Analysis & Reconciliation Engine — Step 2 of the pipeline.

Compares the theoretical SOP steps against the actual financial event log
and produces three classes of findings:

  1. Unmapped Leakage   — recurring vendors with no matching SOP step
                          (money leaving with no documented process).
  2. SOP Deviations      — SOP steps with expected spend but zero matching
                          transactions (documented process, no execution).
  3. Threshold Reviews   — transactions above an SOP approval threshold,
                          flagged for human verification (bank data alone
                          cannot prove approval happened).
  4. Cycle-Time Gaps     — SOP cadence (e.g. monthly) vs. actual payment
                          cadence drifting apart.
"""

import re

import pandas as pd


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower())) - {
        "the", "and", "for", "with", "from", "pay", "payment", "monthly",
        "weekly", "quarterly", "annual", "annually"}


def vendor_matches(step: dict, vendor: str) -> bool:
    """Fuzzy match: does this SOP step plausibly govern this vendor?"""
    haystack = _tokens(
        f"{step.get('expected_vendor', '')} {step.get('action', '')} "
        f"{step.get('trigger', '')}")
    needle = _tokens(vendor)
    if not haystack or not needle:
        return False
    return len(haystack & needle) > 0


def _match_step_to_vendors(step: dict, events: pd.DataFrame) -> pd.DataFrame:
    vendors = events["vendor_payee"].unique()
    matched = [v for v in vendors if vendor_matches(step, v)]
    return events[events["vendor_payee"].isin(matched)]


def find_unmapped_leakage(steps: list[dict],
                          events: pd.DataFrame) -> list[dict]:
    """Recurring spend with no governing SOP step."""
    findings = []
    for vendor, group in events.groupby("vendor_payee"):
        cadence = group["frequency"].iloc[0]
        if cadence == "ad-hoc":
            continue
        if any(vendor_matches(s, vendor) for s in steps):
            continue
        median_amount = float(group["amount"].median())
        dates = group["timestamp"].sort_values().drop_duplicates()
        median_gap = float(dates.diff().dt.days.median()) if len(dates) >= 2 else 30.0
        monthly_cost = median_amount * (30.0 / median_gap) if median_gap else median_amount
        findings.append({
            "type": "unmapped_leakage",
            "vendor": vendor,
            "vendor_display": group["vendor_raw"].iloc[0],
            "cadence": cadence,
            "transactions": len(group),
            "monthly_cost": round(monthly_cost, 2),
            "annualized": round(monthly_cost * 12, 2),
            "category": group["category"].iloc[0],
            "detail": (f"Recurring {cadence} spend with '{group['vendor_raw'].iloc[0]}' "
                       f"has no matching SOP step — undocumented outflow."),
        })
    return sorted(findings, key=lambda f: f["annualized"], reverse=True)


def find_sop_deviations(steps: list[dict],
                        events: pd.DataFrame) -> list[dict]:
    """SOP steps that expect spend but show no transactions."""
    findings = []
    for step in steps:
        if step.get("expected_amount") is None:
            continue  # nothing financial to verify
        matched = _match_step_to_vendors(step, events)
        if matched.empty:
            findings.append({
                "type": "sop_deviation",
                "step_id": step["step_id"],
                "vendor": step.get("expected_vendor", ""),
                "expected_amount": step.get("expected_amount"),
                "detail": (f"{step['step_id']}: '{step.get('action', '')[:60]}' "
                           f"expects ~${step['expected_amount']:,.0f} "
                           f"but no matching transactions found — documented "
                           f"process with no execution."),
            })
    return findings


def find_threshold_reviews(steps: list[dict],
                           events: pd.DataFrame) -> list[dict]:
    """Transactions above SOP approval thresholds (verify approval on file)."""
    findings = []
    for step in steps:
        threshold = step.get("approval_threshold")
        if not threshold:
            continue
        matched = _match_step_to_vendors(step, events)
        # Also scan all large transactions — approval rules are global.
        pool = events if matched.empty else matched
        over = pool[pool["amount"] > threshold]
        for _, txn in over.iterrows():
            findings.append({
                "type": "threshold_review",
                "step_id": step["step_id"],
                "vendor": txn["vendor_raw"],
                "amount": round(float(txn["amount"]), 2),
                "threshold": threshold,
                "date": txn["timestamp"].date().isoformat(),
                "detail": (f"${txn['amount']:,.0f} to '{txn['vendor_raw']}' on "
                           f"{txn['timestamp'].date()} exceeds the "
                           f"${threshold:,.0f} approval threshold in "
                           f"{step['step_id']} — verify approval is on file."),
            })
    return findings


def find_cycle_gaps(steps: list[dict], events: pd.DataFrame) -> list[dict]:
    """SOP cadence vs. actual payment cadence drifting > 1.5x apart."""
    findings = []
    for step in steps:
        expected = step.get("expected_interval_days")
        if not expected:
            continue
        matched = _match_step_to_vendors(step, events)
        if matched.empty:
            continue
        vendor = matched["vendor_payee"].iloc[0]
        dates = matched["timestamp"].sort_values().drop_duplicates()
        if len(dates) < 3:
            continue
        actual = float(dates.diff().dt.days.median())
        if actual > expected * 1.5:
            findings.append({
                "type": "cycle_gap",
                "step_id": step["step_id"],
                "vendor": vendor,
                "expected_every_days": expected,
                "actual_every_days": round(actual, 1),
                "detail": (f"{step['step_id']}: expected every ~{expected} days, "
                           f"actually every ~{actual:.0f} days — execution "
                           f"is drifting from the documented cadence."),
            })
    return findings


def analyze(steps: list[dict], events: pd.DataFrame) -> dict:
    """Run the full gap analysis. Returns findings grouped by type."""
    leakage = find_unmapped_leakage(steps, events)
    return {
        "unmapped_leakage": leakage,
        "sop_deviations": find_sop_deviations(steps, events),
        "threshold_reviews": find_threshold_reviews(steps, events),
        "cycle_gaps": find_cycle_gaps(steps, events),
        "summary": {
            "steps_parsed": len(steps),
            "events_analyzed": len(events),
            "vendors_seen": int(events["vendor_payee"].nunique()),
            "monthly_leakage": round(sum(f["monthly_cost"] for f in leakage), 2),
            "annualized_leakage": round(
                sum(f["annualized"] for f in leakage), 2),
        },
    }
