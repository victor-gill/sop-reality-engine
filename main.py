"""
SOP Reality Engine — Phase 1 (Proof of Concept).

Parses SOP documents, normalizes financial ledgers, compares documented
process against actual spend, and prints an operational reality report.

Usage:
    python main.py --sop sample_data/sample_sop.txt --csv sample_data/sample_bank.csv
    python main.py --csv my_bank_export.csv            # SOP-less mode
    python main.py --sop my_sop.pdf --csv my_bank_export.csv --json out.json

Set ANTHROPIC_API_KEY to use LLM extraction for free-prose SOPs;
otherwise the rules-based parser handles labeled-format SOPs.
"""

import argparse
import json

from sop_parser import parse_sop
from financial_normalizer import normalize_ledger
from gap_engine import analyze
from report import print_report


def main():
    ap = argparse.ArgumentParser(description="SOP Reality Engine — Phase 1 POC")
    ap.add_argument("--sop", default=None,
                    help="Path to SOP (.pdf/.txt/.md). Omit for SOP-less mode: "
                         "the engine infers your actual process from the "
                         "transaction history alone.")
    ap.add_argument("--csv", required=True, help="Path to bank/ledger CSV")
    ap.add_argument("--json", default=None,
                    help="Optional path to write findings as JSON")
    args = ap.parse_args()

    steps = parse_sop(args.sop) if args.sop else []
    sop_less = args.sop is None
    if sop_less:
        print("[main] SOP-less mode: inferring process from transactions alone.")
    if args.sop and not steps:
        print("No SOP steps extracted — check the input format.")
        return

    events = normalize_ledger(args.csv)
    analysis = analyze(steps, events)
    print_report(analysis, sop_less=sop_less)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, default=str)
        print(f"\nFindings written to {args.json}")


if __name__ == "__main__":
    main()
