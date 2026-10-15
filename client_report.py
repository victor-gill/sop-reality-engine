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


def _evidence(finding: dict) -> str:
    """One verifiable line per finding type — the client should be able
    to check it in 30 seconds."""
    t = finding.get("type")
    if t == "unmapped_leakage":
        return (f"Evidence: {finding['transactions']} transactions, "
                f"{finding['first_date']} → {finding['last_date']}, "
                f"amounts {_money(finding['min_amount'])}–"
                f"{_money(finding['max_amount'])}, cadence regularity: "
                f"{finding['regularity']}.")
    if t == "threshold_review":
        return (f"Evidence: {_money(finding['amount'])} to "
                f"'{finding['vendor']}' on {finding['date']} "
                f"(threshold {_money(finding['threshold'])}).")
    if t == "payroll_gap":
        return (f"Evidence: {_money(finding['amount'])} — "
                f"'{finding['payee']}' on {finding['date']}.")
    if t == "pnl_variance":
        return (f"Evidence: books {_money(finding['pnl_total'])} vs. bank "
                f"{_money(finding['bank_total'])} for "
                f"'{finding['account']}' ({finding['period']}).")
    if t == "cycle_gap":
        return (f"Evidence: expected every {finding['expected_every_days']} "
                f"days, actually every "
                f"{finding['actual_every_days']:.0f} days.")
    if t == "sop_deviation":
        return (f"Evidence: {finding['step_id']} expects "
                f"~{_money(finding['expected_amount'])}; no matching "
                f"transactions in the data window.")
    return ""


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
    ]
    ev = _evidence(finding)
    if ev:
        lines += [ev, ""]
    lines.append(f"**Suggested owner:** {owner}")
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


def count_edits(markdown: str) -> int:
    """Unfilled EDIT markers remaining in the report."""
    return markdown.count(EDIT.strip())


def build_report(findings: dict, tasks: list | None,
                 business: str, final: bool = False) -> str:
    s = findings["summary"]
    today = date.today().isoformat()
    L = [
        f"# Operational Audit Report — {business}",
        "",
        f"**Date:** {today}",
        "**Prepared by:**",
        f"{EDIT}your name / firm",
        "**Documents reviewed:**",
        f"{EDIT}list the source files and their periods, e.g. bank export "
        "Jan–Jun 2026, payroll ledger, P&L",
        f"**Method:** automated reconciliation of documented process "
        f"against {s['events_analyzed']} transactions across "
        f"{s['vendors_seen']} vendors, followed by manual auditor review "
        f"of every finding.",
        "",
    ]
    if not final:
        L += [
            "**Status:** DRAFT — not for client delivery. The auditor has "
            "not yet completed every EDIT prompt in this document.",
            "",
        ]
    L += [
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


def _rich(paragraph, text: str):
    """Add runs to a paragraph, honoring **bold** spans."""
    import re
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not part:
            continue
        run = paragraph.add_run(part)
        if i % 2 == 1:
            run.bold = True


def to_docx(markdown: str, path: str):
    """Minimal markdown → .docx so the deliverable looks finished.
    Handles headings, bold, bullets, checkboxes, blockquotes, and rules."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    style = doc.styles["Normal"]
    style.font.size = Pt(11)

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line == "---":
            doc.add_paragraph().add_run().add_break()
        elif line.startswith(("- [ ]", "- [x]")):
            p = doc.add_paragraph(style="List Bullet")
            _rich(p, line[5:].replace("&nbsp;", " "))
        elif line.startswith("- "):
            p = doc.add_paragraph(style="List Bullet")
            _rich(p, line[2:])
        elif line.startswith("> "):
            p = doc.add_paragraph(style="Intense Quote")
            run = p.add_run(line[2:])
            run.italic = True
            run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        else:
            p = doc.add_paragraph()
            _rich(p, line.replace("&nbsp;", " "))
    doc.save(path)


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
    ap.add_argument("--final", action="store_true",
                    help="refuse to write while any EDIT markers remain")
    ap.add_argument("--format", choices=["md", "docx"], default="md",
                    help="output format: Markdown (edit) or Word (deliver)")
    args = ap.parse_args()

    findings = _load(args.findings)
    tasks = _load(args.tasks)
    business = args.business
    if not business and args.config:
        from config import load_config
        business = load_config(args.config).get("business_name")
    report = build_report(findings, tasks, business or "Client Business",
                          final=args.final)
    n_edits = count_edits(report)
    if args.final and n_edits:
        ap.error(f"refusing: {n_edits} EDIT markers remain — "
                 "complete the auditor pass first")
    if args.format == "docx":
        out = args.out
        if not out.lower().endswith(".docx"):
            out += ".docx"
        to_docx(report, out)
    else:
        out = args.out
        Path(out).write_text(report, encoding="utf-8")
    n = sum(len(findings[k]) for k in findings if k != "summary")
    print(f"[client_report] {n} findings written to {out} — "
          f"edit the {EDIT.strip()} lines.")


if __name__ == "__main__":
    main()
