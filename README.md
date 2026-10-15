# SOP Reality Engine — Phase 1 (Proof of Concept)

Parses SOP documents, normalizes financial ledgers, compares documented
process against actual spend, and prints an operational reality report.

This is the automated version of an operational audit: the SOP says what
*should* happen, the bank statement says what *actually* happened, and the
gap between them is where money leaks.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run it

```bash
# Phase 1: terminal report — 1 to 4 input documents
.venv/bin/python main.py --sop sample_data/sample_sop.txt \
    --bank sample_data/sample_bank.csv \
    --payroll sample_data/sample_payroll.csv \
    --pnl sample_data/sample_pnl.csv \
    --logistics sample_data/sample_logistics.csv
.venv/bin/python main.py --bank my_bank_export.csv            # SOP-less mode: no SOP needed
.venv/bin/python main.py --sop my_sop.pdf --bank my_bank_export.csv --json findings.json

# Phase 3: web UI
.venv/bin/streamlit run app.py

# Phase 6: the product loop — schedule this (e.g. cron: 0 8 * * 1)
.venv/bin/python runner.py --sop sample_data/sample_sop.txt \
    --bank sample_data/sample_bank.csv \
    --payroll sample_data/sample_payroll.csv \
    --pnl sample_data/sample_pnl.csv \
    --logistics sample_data/sample_logistics.csv

# Tests
.venv/bin/python -m pytest tests/ -q
```

## Privacy & demo notes

- Uploaded files are processed in your session and **deleted after the
  run** — nothing is stored.
- Every visitor gets an isolated session: uploads, settings, approvals,
  and tasks never touch another session.
- The hosted demo is synthetic-data-only (sample dataset). The scheduled
  loop (`runner.py` + digest + approvals) runs on your own machine or
  VPS; the app will show its digest banner when it finds a local
  `state/digest.json`.
- This is a demo/POC, not production software: no auth, no tenant
  isolation, no real payment rails (auto-pay marks intent; it never
  moves money).

## What it finds

1. **Unmapped leakage** — recurring vendors with no governing SOP step
   (money leaving with no documented process).
2. **SOP deviations** — SOP steps that expect spend but show zero
   transactions (documented process, no execution).
3. **Threshold reviews** — transactions above SOP approval thresholds,
   flagged for human verification.
4. **Cycle-time gaps** — documented cadence (e.g. monthly) drifting from
   actual payment cadence.
5. **Payroll reconciliation** — payroll records with no matching bank
   payment (unallocated/missing funds) and bank payroll payments with no
   payroll record (unknown charges).
6. **P&L variance** — accounts where the books diverge from actual bank
   spend by period (miscategorization, missing transactions).

## Inputs

- **SOP**: `.pdf`, `.txt`, or `.md`. Two parsing modes:
  - *Labeled format* (no API key): `STEP 1` headers with `Trigger:` /
    `Action:` / `Condition:` / `Role:` lines — see
    `sample_data/sample_sop.txt`.
  - *Free prose* (needs `ANTHROPIC_API_KEY`): any SOP document; Claude
    extracts the structured steps.
- **Bank CSV** (`--bank`, required): flexible headers — auto-detects
  date/payee/amount columns (`Date`, `Description`, `Amount`, etc.).
- **Payroll CSV** (`--payroll`, optional): employee, pay date, net/gross
  pay. Joins the event log and powers payroll reconciliation.
- **P&L export** (`--pnl`, optional): transaction detail *or* an account ×
  period matrix (`Account, Jan 2026, Feb 2026, ...`).
- **Logistics CSV** (`--logistics`, optional): shipment date, carrier,
  cost, origin, destination. Another dataset, same shape — timestamped
  cost events that flow into the full analysis.

## Sample output

```
1. UNMAPPED LEAKAGE ($207.08/mo  |  $2,485.04/yr)
  [!!] DataHaven Backup — monthly, 6 transactions
       ~$144.19/mo (~$1,730.32/yr)  [Software/SaaS]
```

## Roadmap

- **Phase 1** ✅ — SOP parser + ledger normalizer + gap engine + terminal report.
- **Phase 2** ✅ — `langgraph_generator.py`: turns reconciled SOP steps into a
  runnable LangGraph workflow (`workflow.py`) with per-step Pydantic schemas,
  approval-threshold routing, and human-in-the-loop checkpoints. The generated
  file executes standalone — demo included.
- **Phase 3** ✅ — `app.py` (Streamlit): drag-and-drop SOP + bank CSV, findings
  dashboard, Visual Reality Map (SOP steps → spend, unmapped in red), and
  one-click agent workflow generation with download.
- **Phase 4** ✅ — `task_queue.py`: every finding becomes a routed,
  prioritized task (P1 threshold breaches → Operator, deviations → the
  step's role owner). Tasks carry a concrete first action and a status
  lifecycle (open → in_progress → resolved). Surfaced as the Task Queue
  tab in the UI — the meeting killer.
- **SOP-less mode** ✅ — most SMBs have no written SOPs, so `--bank` alone
  infers the actual process from transaction history. Strongest wedge for
  the free-audit offer.
- **Phase 5** ✅ — multi-document audits: `--payroll` (payroll
  reconciliation — records without payments, payments without records),
  `--pnl` (books-vs-bank variance by account and period), and
  `--logistics` (shipments as another dataset, same event shape). New
  findings route into the Task Queue (P1 → Payroll Lead, variance →
  Controller).
- **Phase 6** ✅ — the product loop. `runner.py` runs the whole system on
  a schedule (cron): audit → tasks → agent workflow → owner digest. The
  generated workflow's human-in-the-loop checkpoint now writes an
  approval request to `state/approvals/pending/` instead of a terminal
  prompt — the owner approves/rejects from the app's Approvals tab and
  `resolve_approval` completes the workflow. The app shows the
  Tuesday-morning digest banner.
- **Customizable** ✅ — `business_config.yaml` is the single source of
  truth: approval threshold (global + per-category overrides), auto-pay
  vendors, always-review vendors, team roles, digest cadence. The
  workflow reads it at runtime — change a value, the next run follows
  it, no regeneration. The app's Settings tab edits it with no code.
  Point `$SOPRE_CONFIG` at a different file for a different business.
