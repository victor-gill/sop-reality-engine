"""
Scheduled runner — the product loop.

Designed for cron (e.g. `0 8 * * 1` for a Monday-morning run):
reads the inbox documents, runs the audit pipeline, routes findings
into the task queue, executes new transactions through the agent
workflow, and writes the owner digest.

State lives in ./state/ as plain JSON so the app can render it:
    state/ledger.json              # every processed transaction + outcome
    state/approvals/pending/*.json # waiting on the owner (approve in app)
    state/approvals/decided/*.json# owner decisions
    state/tasks.json               # routed task queue
    state/digest.json              # the Tuesday-morning brief
    state/findings.json            # latest full analysis

Usage:
    python runner.py --sop sample_data/sample_sop.txt \
        --bank sample_data/sample_bank.csv \
        --payroll sample_data/sample_payroll.csv \
        --pnl sample_data/sample_pnl.csv \
        --logistics sample_data/sample_logistics.csv
    python runner.py --bank bank.csv   # SOP-less mode
"""

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from sop_parser import parse_sop
from financial_normalizer import (normalize_ledger, normalize_payroll,
                                  normalize_pnl, normalize_logistics)
from gap_engine import analyze
from task_queue import generate_tasks
from langgraph_generator import generate_workflow
from config import load_config, display_name

HERE = Path(__file__).parent
STATE = HERE / "state"
WORKFLOW_PATH = HERE / "state" / "workflow.py"


def _txn_id(row) -> str:
    """Deterministic id so re-runs never double-process a transaction."""
    raw = f"{row['timestamp']}|{row['vendor_raw']}|{row['amount']:.2f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _load_workflow(config_path: str | None = None):
    import os
    if config_path:
        os.environ["SOPRE_CONFIG"] = str(config_path)
    spec = importlib.util.spec_from_file_location(
        "sop_workflow", str(WORKFLOW_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser(description="SOP Reality Engine — runner")
    ap.add_argument("--sop", default=None)
    ap.add_argument("--bank", default=None)
    ap.add_argument("--csv", default=None, help="alias for --bank")
    ap.add_argument("--payroll", default=None)
    ap.add_argument("--pnl", default=None)
    ap.add_argument("--logistics", default=None)
    ap.add_argument("--config", default=None, help="business config YAML")
    args = ap.parse_args()

    bank_path = args.bank or args.csv
    if not bank_path:
        ap.error("--bank is required")
    cfg = load_config(args.config)
    now = datetime.now(timezone.utc).isoformat()

    # --- 1. Audit ---------------------------------------------------------
    steps = parse_sop(args.sop) if args.sop else []
    frames = [normalize_ledger(bank_path)]
    payroll = pd.DataFrame()
    if args.payroll:
        payroll = normalize_payroll(args.payroll)
        frames.append(payroll)
    if args.logistics:
        frames.append(normalize_logistics(args.logistics))
    events = pd.concat(frames, ignore_index=True).sort_values("timestamp")
    pnl = normalize_pnl(args.pnl) if args.pnl else pd.DataFrame()
    analysis = analyze(steps, events, payroll=payroll, pnl=pnl)
    tasks = generate_tasks(analysis, steps)
    for t in tasks:
        t["assignee"] = display_name(cfg, t["assignee"])
    _write(STATE / "findings.json", analysis)
    _write(STATE / "tasks.json", tasks)

    # --- 2. Agent workflow ------------------------------------------------
    generate_workflow(steps, out_path=str(WORKFLOW_PATH))
    wf = _load_workflow(args.config or str(HERE / "business_config.yaml"))
    approvals_dir = str(STATE / "approvals")

    ledger_path = STATE / "ledger.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    auto_paid_total, auto_paid_n, new_pending, duplicates = 0.0, 0, 0, 0
    for _, row in events.iterrows():
        tid = _txn_id(row)
        if tid in ledger:
            # Same economic transaction seen in another document
            # (e.g. payroll record mirroring a bank payment) — process once.
            duplicates += 1
            continue
        result = wf.run_transaction(
            vendor_name=row["vendor_raw"], amount=float(row["amount"]),
            txn_id=tid, approvals_dir=approvals_dir,
            memo="", category=row.get("category", ""))
        outcome = result["outcome"]
        ledger[tid] = {
            "txn_id": tid,
            "vendor": row["vendor_raw"],
            "amount": float(row["amount"]),
            "date": str(row["timestamp"].date()),
            "category": row.get("category", ""),
            "outcome": outcome,
            "processed_at": now,
        }
        if outcome == "AUTO_PAID":
            auto_paid_total += float(row["amount"])
            auto_paid_n += 1
        elif outcome == "PENDING_APPROVAL":
            new_pending += 1
    _write(ledger_path, ledger)

    # --- 3. Digest ----------------------------------------------------------
    pending = sorted((STATE / "approvals" / "pending").glob("*.json")) \
        if (STATE / "approvals" / "pending").exists() else []
    s = analysis["summary"]
    digest = {
        "business": cfg.get("business_name"),
        "run_at": now,
        "headline": (
            f"{auto_paid_n} payments auto-processed "
            f"(${auto_paid_total:,.0f}); {len(pending)} need your decision; "
            f"${s['annualized_leakage']:,.0f}/yr unmapped leakage found."
        ),
        "auto_paid": {"count": auto_paid_n, "total": round(auto_paid_total, 2)},
        "pending_approvals": [
            {"txn_id": p.stem, **json.loads(p.read_text())} for p in pending
        ],
        "annualized_leakage": s["annualized_leakage"],
        "open_tasks": len([t for t in tasks if t["status"] == "open"]),
        "top_tasks": tasks[:5],
    }
    _write(STATE / "digest.json", digest)

    print(f"[runner] {cfg.get('business_name')}: "
          f"{auto_paid_n} auto-paid (${auto_paid_total:,.0f}), "
          f"{len(pending)} pending approval, "
          f"{len(tasks)} tasks routed."
          + (f" {duplicates} cross-document duplicates skipped."
             if duplicates else ""))
    print(f"[runner] Digest: {digest['headline']}")


if __name__ == "__main__":
    main()
