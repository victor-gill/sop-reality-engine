"""
Client report generator — findings JSON in, editable report out.

The engine fills in every fact; you fill in the judgment. Each finding
ships with its evidence pre-written and blank Auditor assessment /
Recommendation lines marked EDIT. Work through the validation checklist
(docs/audit-validation-checklist.md) as you edit.

Usage:
    python client_report.py --findings findings.json --out client-report.md
    python client_report.py --findings findings.json --tasks tasks.json \
        --config business_config.yaml --out client-report.md
"""

import argparse
import json
from datetime import date
from pathlib import Path

EDIT = "> ✏️ EDIT: "


def _money(x) -> str:
    return f"${x:,.2f}"


def _load(path):
    return json.loads(Path(path).read_text()) if path else None


def _task_for(tasks, finding) -> dict | None:
    """Best-effort match of a task to its finding for the pre-filled action."""
    if not tasks:
        return None
    ftype = finding.get("type", "")
    for t in tasks:
        if t.get("type") != ftype:
            continue
        title = t.get("title", "").lower()
        for key in ("vendor_display", "vendor", "payee", "account", "step_id"):
            val = str(finding.get(key, "")).lower()
            if val and len(val) > 3 and val in title:
                return t
    return None


def _finding_block(finding, tasks, num) -> list[str]:
    t = _task_for(tasks, finding)
    action = t["first_action"] if t else ""
    owner = t["assignee"] if t else finding.get("assigned_role", "Owner")
    lines = [
        f"### Finding {num}",
        "",
        f"**What the engine found:** {finding.get('detail', '')}",
        "",
        f"**Suggested owner:** {owner}",
    ]
    if action:
        lines.append(f"**Suggested first step:** {action}")
    lines += [
        "",
        f"{EDIT}auditor assessment — confirm, dismiss (with reason), or "
        "escalate with a specific client question.",
        "",
        f"{EDIT}recommendation — what the client should do, by when.",
        "",
        "- [ ] Confirmed &nbsp; - [ ] Dismissed &nbsp; - [ ] Escalated",
        "",
        "---",
        "",
    ]
    return lines


def build_report(findings: dict, tasks: list | None,
                 business: str) -> str:
    s = findings["summary"]
    today = date.today().isoformat()
    L = [
        f"# Operational Audit Report — {business}",
        "",
        f"**Date:** {today}",
        f"**Prepared by:** {EDIT}your name / firm",
        f"**Documents reviewed:** {EDIT}list the source files and their "
        "periods, e.g. bank export Jan–Jun 2026, payroll ledger, P&L",
        f"**Method:** automated reconciliation of documented process "
        f"against {s['events_analyzed']} transactions across "
        f"{s['vendors_seen']} vendors, followed by manual auditor review "
        f"of every finding.",
        "",
        "## Executive summary",
        "",
        f"{EDIT}one paragraph: the headline numbers below, in plain "
        "English, plus the single most important thing the owner should "
        "do this month.",
        "",
        f"- **Annualized unmapped spend:** {_money(s['annualized_leakage'])} "
        f"({_money(s['monthly_leakage'])}/mo with no documented process)",
        f"- **SOP deviations:** {len(findings['sop_deviations'])}",
        f"- **Threshold reviews:** {len(findings['threshold_reviews'])}",
        f"- **Cycle-time gaps:** {len(findings['cycle_gaps'])}",
    ]
    if s.get("payroll_provided"):
        L.append(f"- **Payroll gaps:** {len(findings['payroll_gaps'])}")
    if s.get("pnl_provided"):
        L.append(f"- **P&L variance flags:** {len(findings['pnl_variance'])}")
    L += ["", "---", ""]

    sections = [
        ("Unmapped spend — money leaving with no documented process",
         findings["unmapped_leakage"]),
        ("SOP deviations — documented process, no execution",
         findings["sop_deviations"]),
        ("Threshold reviews — verify approval on file",
         findings["threshold_reviews"]),
        ("Cycle-time gaps — cadence drift",
         findings["cycle_gaps"]),
    ]
    if s.get("payroll_provided"):
        sections.append(("Payroll reconciliation",
                         findings["payroll_gaps"]))
    if s.get("pnl_provided"):
        sections.append(("P&L variance — books vs. bank",
                         findings["pnl_variance"]))

    num = 0
    for title, items in sections:
        L += [f"## {title}", ""]
        if not items:
            L += ["_None found._", ""]
            continue
        for f in items:
            num += 1
            L += _finding_block(f, tasks, num)

    L += [
        "## Questions for the client",
        "",
        f"{EDIT}list every escalated finding as a specific question — "
        '"was the $X payment to Y on [date] approved, and by whom?" — '
        "not \"please review.\"",
        "",
        "## Methodology & limitations",
        "",
        "Findings were produced by reconciling your documents against "
        "each other: SOP steps vs. bank transactions, payroll records vs. "
        "bank payments, and P&L account totals vs. bank activity by "
        "period. Every finding was then reviewed by the auditor against "
        "the validation checklist.",
        "",
        "Known limitations: recurring charges are inferred from timing "
        "patterns and can misread annual renewals or seasonal vendors — "
        "each was checked. P&L comparisons assume the books and the bank "
        "cover the same periods on the same (cash) basis; accrual timing "
        "differences were screened out where identified.",
        "",
        f"{EDIT}add anything specific to this engagement.",
        "",
    ]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Generate an editable client report from findings JSON")
    ap.add_argument("--findings", required=True)
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--config", default=None,
                    help="business_config.yaml (for the business name)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--business", default=None,
                    help="overrides the business name")
    args = ap.parse_args()

    findings = _load(args.findings)
    tasks = _load(args.tasks)
    business = args.business
    if not business and args.config:
        from config import load_config
        business = load_config(args.config).get("business_name")
    report = build_report(findings, tasks, business or "Client Business")
    Path(args.out).write_text(report, encoding="utf-8")
    n = sum(len(findings[k]) for k in findings if k != "summary")
    print(f"[client_report] {n} findings written to {args.out} — "
          f"edit the {EDIT.strip()} lines.")


if __name__ == "__main__":
    main()
