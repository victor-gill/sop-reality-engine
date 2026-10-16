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
        monthly_raw = median_amount * (30.0 / median_gap) if median_gap else median_amount
        monthly_cost = round(monthly_raw, 2)
        # Annualized derives from the ROUNDED monthly so the two always
        # reconcile: monthly x 12 == annualized, to the cent.
        annualized = round(monthly_cost * 12, 2)
        # Evidence the client can verify in 30 seconds, plus a regularity
        # cue for triage (high = metronome, low = likely false alarm).
        gaps = dates.diff().dt.days.dropna()
        if len(gaps) >= 2 and gaps.mean():
            cv = gaps.std() / gaps.mean()
            regularity = ("high" if cv < 0.25 else
                          "medium" if cv < 0.6 else "low")
        else:
            regularity = "n/a"
        findings.append({
            "type": "unmapped_leakage",
            "vendor": vendor,
            "vendor_display": group["vendor_raw"].iloc[0],
            "cadence": cadence,
            "transactions": len(group),
            "monthly_cost": monthly_cost,
            "annualized": annualized,
            "category": group["category"].iloc[0],
            "first_date": dates.iloc[0].date().isoformat(),
            "last_date": dates.iloc[-1].date().isoformat(),
            "min_amount": round(float(group["amount"].min()), 2),
            "max_amount": round(float(group["amount"].max()), 2),
            "regularity": regularity,
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


def analyze(steps: list[dict], events: pd.DataFrame,
            payroll: pd.DataFrame | None = None,
            pnl: pd.DataFrame | None = None) -> dict:
    """Run the full gap analysis. Returns findings grouped by type."""
    leakage = find_unmapped_leakage(steps, events)
    bank_only = events[events["source"] == "bank"] if "source" in events.columns else events
    payroll_gaps = (find_payroll_gaps(payroll, bank_only)
                    if payroll is not None and not payroll.empty else [])
    pnl_variance = (find_pnl_variance(pnl, bank_only)
                    if pnl is not None and not pnl.empty else [])
    monthly = round(sum(f["monthly_cost"] for f in leakage), 2)
    # Spend by category — the auditor's pie chart. Raw totals; the report
    # decides what counts as auxiliary vs. internal transfers.
    cat = events.groupby("category")["amount"].agg(["sum", "count"])
    spending_by_category = {
        str(c): {"total": round(float(r["sum"]), 2),
                 "transactions": int(r["count"])}
        for c, r in cat.iterrows()
    }
    # Business-vs-personal split, when the classifier has run. Empty
    # dict when events were never classified — the report skips it.
    expense_breakdown = {}
    if "expense_class" in events.columns:
        grp = events.groupby("expense_class")["amount"].agg(["sum", "size"])
        expense_breakdown = {
            str(cls): {"total": round(float(r["sum"]), 2),
                       "transactions": int(r["size"])}
            for cls, r in grp.iterrows()
        }
    return {
        "unmapped_leakage": leakage,
        "sop_deviations": find_sop_deviations(steps, events),
        "threshold_reviews": find_threshold_reviews(steps, events),
        "cycle_gaps": find_cycle_gaps(steps, events),
        "payroll_gaps": payroll_gaps,
        "pnl_variance": pnl_variance,
        "summary": {
            "steps_parsed": len(steps),
            "events_analyzed": len(events),
            "vendors_seen": int(events["vendor_payee"].nunique()),
            # Annualized derives from the rounded monthly total so the
            # headline always reconciles: monthly x 12 == annualized.
            "monthly_leakage": monthly,
            "annualized_leakage": round(monthly * 12, 2),
            "sources": sorted(events["source"].unique().tolist())
            if "source" in events.columns else ["bank"],
            "payroll_provided": payroll is not None and not payroll.empty,
            "pnl_provided": pnl is not None and not pnl.empty,
            "spending_by_category": spending_by_category,
            "expense_breakdown": expense_breakdown,
            "period_start": events["timestamp"].min().date().isoformat()
            if len(events) else None,
            "period_end": events["timestamp"].max().date().isoformat()
            if len(events) else None,
        },
    }


# ---------------------------------------------------------------------------
# Cross-source reconciliation
# ---------------------------------------------------------------------------

def _close_match(txn, pool, day_tol=5, amt_tol=0.02):
    """Find pool events within +/- days and amount tolerance."""
    lo, hi = txn["timestamp"] - pd.Timedelta(days=day_tol), txn["timestamp"] + pd.Timedelta(days=day_tol)
    cands = pool[(pool["timestamp"] >= lo) & (pool["timestamp"] <= hi)]
    return cands[abs(cands["amount"] - txn["amount"]) / txn["amount"] <= amt_tol]


def find_payroll_gaps(payroll: pd.DataFrame,
                      bank: pd.DataFrame) -> list[dict]:
    """Payroll records with no matching bank payment, and vice versa."""
    findings = []
    for _, p in payroll.iterrows():
        if _close_match(p, bank).empty:
            findings.append({
                "type": "payroll_gap",
                "direction": "missing_payment",
                "payee": p["vendor_raw"],
                "amount": round(float(p["amount"]), 2),
                "date": p["timestamp"].date().isoformat(),
                "detail": (f"Payroll shows ${p['amount']:,.2f} to "
                           f"'{p['vendor_raw']}' on {p['timestamp'].date()} "
                           f"with no matching bank payment — unallocated or "
                           f"missing funds."),
            })
    bank_payroll = bank[bank["category"].isin(["Payroll", "Contractor"])]
    for _, b in bank_payroll.iterrows():
        if _close_match(b, payroll).empty:
            findings.append({
                "type": "payroll_gap",
                "direction": "missing_record",
                "payee": b["vendor_raw"],
                "amount": round(float(b["amount"]), 2),
                "date": b["timestamp"].date().isoformat(),
                "detail": (f"Bank shows payroll-category payment of "
                           f"${b['amount']:,.2f} to '{b['vendor_raw']}' on "
                           f"{b['timestamp'].date()} with no payroll record — "
                           f"unknown charge."),
            })
    return findings


def find_pnl_variance(pnl: pd.DataFrame, bank: pd.DataFrame,
                      tol: float = 0.15) -> list[dict]:
    """P&L account totals vs. actual bank spend per period.

    Flags accounts where the books diverge from the bank by more than
    `tol` — miscategorization, missing transactions, or timing games.
    """
    findings = []
    bank = bank.copy()
    bank["period"] = bank["timestamp"].dt.to_period("M").dt.to_timestamp()
    bank_monthly = bank.groupby(["vendor_payee", "period"])["amount"].sum()
    for _, row in pnl.iterrows():
        # Match P&L account to bank vendors by token overlap.
        acct_tokens = _tokens(row["account_raw"])
        matched_vendors = [v for v in bank["vendor_payee"].unique()
                           if acct_tokens & _tokens(v)]
        bank_total = sum(bank_monthly.get((v, row["period"]), 0)
                         for v in matched_vendors)
        pnl_total = row["amount"]
        if pnl_total < 100:
            continue
        variance = abs(bank_total - pnl_total) / pnl_total
        if variance > tol:
            direction = "overstated" if pnl_total > bank_total else "understated"
            findings.append({
                "type": "pnl_variance",
                "account": row["account_raw"],
                "period": row["period"].strftime("%b %Y"),
                "pnl_total": round(pnl_total, 2),
                "bank_total": round(bank_total, 2),
                "variance_pct": round(variance * 100, 1),
                "detail": (f"P&L {direction} '{row['account_raw']}' for "
                           f"{row['period'].strftime('%b %Y')}: books say "
                           f"${pnl_total:,.2f}, bank shows "
                           f"${bank_total:,.2f} "
                           f"({variance * 100:.0f}% variance)."),
            })
    return sorted(findings, key=lambda f: f["variance_pct"], reverse=True)
