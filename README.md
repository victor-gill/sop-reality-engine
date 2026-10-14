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
# Phase 1: terminal report
.venv/bin/python main.py --sop sample_data/sample_sop.txt --csv sample_data/sample_bank.csv
.venv/bin/python main.py --csv my_bank_export.csv            # SOP-less mode: no SOP needed
.venv/bin/python main.py --sop my_sop.pdf --csv my_bank_export.csv --json findings.json

# Phase 3: web UI
.venv/bin/streamlit run app.py
```

## What it finds

1. **Unmapped leakage** — recurring vendors with no governing SOP step
   (money leaving with no documented process).
2. **SOP deviations** — SOP steps that expect spend but show zero
   transactions (documented process, no execution).
3. **Threshold reviews** — transactions above SOP approval thresholds,
   flagged for human verification.
4. **Cycle-time gaps** — documented cadence (e.g. monthly) drifting from
   actual payment cadence.

## Inputs

- **SOP**: `.pdf`, `.txt`, or `.md`. Two parsing modes:
  - *Labeled format* (no API key): `STEP 1` headers with `Trigger:` /
    `Action:` / `Condition:` / `Role:` lines — see
    `sample_data/sample_sop.txt`.
  - *Free prose* (needs `ANTHROPIC_API_KEY`): any SOP document; Claude
    extracts the structured steps.
- **Bank CSV**: flexible headers — auto-detects date/payee/amount columns
  (`Date`, `Description`, `Amount`, etc.).

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
- **SOP-less mode** ✅ — most SMBs have no written SOPs, so `--csv` alone
  infers the actual process from transaction history. Strongest wedge for
  the free-audit offer.
