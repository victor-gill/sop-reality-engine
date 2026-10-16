"""
SOP Reality Engine — Phase 1 (Proof of Concept).

Parses SOP documents, normalizes operational datasets (bank, payroll, P&L,
logistics), compares documented process against reality, and prints an
operational reality report.

Usage:
    python main.py --sop sample_data/sample_sop.txt --bank sample_data/sample_bank.csv
    python main.py --bank bank.csv --payroll payroll.csv --pnl pnl.csv --logistics shipments.csv
    python main.py --bank my_bank_export.csv            # SOP-less mode
    python main.py --sop my_sop.pdf --bank my_bank_export.csv --json out.json

Set ANTHROPIC_API_KEY to use LLM extraction for free-prose SOPs;
otherwise the rules-based parser handles labeled-format SOPs.
"""

import argparse
import json
import os

import pandas as pd

from sop_parser import parse_sop
from financial_normalizer import (normalize_ledger, normalize_payroll,
                                  normalize_pnl, normalize_logistics)
from gap_engine import analyze
from report import print_report


def main():
    ap = argparse.ArgumentParser(description="SOP Reality Engine — Phase 1 POC")
    ap.add_argument("--sop", default=None,
                    help="Path to SOP (.pdf/.txt/.md). Omit for SOP-less mode: "
                         "the engine infers your actual process from the "
                         "transaction history alone.")
    ap.add_argument("--csv", default=None,
                    help="Path to bank/ledger CSV (legacy alias for --bank)")
    ap.add_argument("--bank", default=None, help="Path to bank/ledger CSV")
    ap.add_argument("--payroll", default=None,
                    help="Path to payroll CSV (employee, pay date, net pay)")
    ap.add_argument("--pnl", default=None,
                    help="Path to P&L export (transaction detail or "
                         "account x period matrix)")
    ap.add_argument("--logistics", default=None,
                    help="Path to logistics/shipments CSV (date, carrier, "
                         "cost, origin, destination)")
    ap.add_argument("--json", default=None,
                    help="Optional path to write findings as JSON")
    ap.add_argument("--config", default=None,
                    help="business_config.yaml to use (default: the repo's)")
    ap.add_argument("--no-classify", action="store_true",
                    help="Skip business-vs-personal expense classification")
    ap.add_argument("--report", default=None,
                    help="Optional path to write an editable client report "
                         "(Markdown, findings pre-filled, judgment left blank)")
    ap.add_argument("--report-format", choices=["md", "docx"], default="md",
                    help="Client report format: Markdown (edit) or Word "
                         "(deliver)")
    ap.add_argument("--report-final", action="store_true",
                    help="Refuse to write the report while any EDIT "
                         "markers remain (stamps DRAFT otherwise)")
    args = ap.parse_args()

    bank_path = args.bank or args.csv
    if not bank_path:
        ap.error("a bank/ledger CSV is required (--bank or --csv)")

    steps = parse_sop(args.sop) if args.sop else []
    sop_less = args.sop is None
    if sop_less:
        print("[main] SOP-less mode: inferring process from transactions alone.")
    if args.sop and not steps:
        print("No SOP steps extracted — check the input format.")
        return

    frames = [normalize_ledger(bank_path).assign(source="bank")]
    payroll = pd.DataFrame()
    if args.payroll:
        payroll = normalize_payroll(args.payroll)
        frames.append(payroll)
    if args.logistics:
        frames.append(normalize_logistics(args.logistics))
    events = pd.concat(frames, ignore_index=True).sort_values("timestamp")

    pnl = normalize_pnl(args.pnl) if args.pnl else pd.DataFrame()

    from task_queue import generate_tasks as _gen_tasks
    from config import load_config as _load_config

    cfg = _load_config(args.config)
    if not args.no_classify:
        from expense_classifier import classify_expenses
        events = classify_expenses(events, cfg,
                                   allow_llm=bool(os.environ.get(
                                       "ANTHROPIC_API_KEY")))

    analysis = analyze(steps, events, payroll=payroll, pnl=pnl)
    print_report(analysis, sop_less=sop_less)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, default=str)
        print(f"\nFindings written to {args.json}")

    if args.report:
        from client_report import build_report, count_edits, to_docx
        from pathlib import Path as _Path
        tasks = _gen_tasks(analysis, steps)
        _out = _Path(args.report)
        _chart = str(_out.with_name(_out.stem + "_spending.png"))
        report_md = build_report(analysis, tasks,
                                 cfg.get("business_name", "Client Business"),
                                 final=args.report_final, cfg=cfg,
                                 chart_path=_chart)
        n_edits = count_edits(report_md)
        if args.report_final and n_edits:
            ap.error(f"refusing: {n_edits} EDIT markers remain — "
                     "complete the auditor pass first")
        out = args.report
        if args.report_format == "docx":
            out = str(_Path(args.report).with_suffix(".docx"))
            to_docx(report_md, out, image_dir=_out.parent)
        else:
            with open(out, "w", encoding="utf-8") as f:
                f.write(report_md)
        n = sum(len(analysis[k]) for k in analysis if k != "summary")
        print(f"Client report drafted at {out} ({n} findings — "
              f"edit the marked lines).")


if __name__ == "__main__":
    main()
