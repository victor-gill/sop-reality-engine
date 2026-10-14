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

    analysis = analyze(steps, events, payroll=payroll, pnl=pnl)
    print_report(analysis, sop_less=sop_less)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, default=str)
        print(f"\nFindings written to {args.json}")


if __name__ == "__main__":
    main()
