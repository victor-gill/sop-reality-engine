"""
SOP Reality Engine — Phase 3 web UI.

Run:  streamlit run app.py
"""

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from sop_parser import parse_sop
from financial_normalizer import (normalize_ledger, normalize_payroll,
                                  normalize_pnl, normalize_logistics)
from gap_engine import analyze, _match_step_to_vendors
from langgraph_generator import generate_workflow
from task_queue import generate_tasks, STATUSES

st.set_page_config(page_title="SOP Reality Engine", layout="wide")
st.title("SOP Reality Engine")
st.caption("What your SOPs say vs. what your money does — "
           "the gap is where profit leaks.")

SAMPLE_SOP = Path(__file__).parent / "sample_data" / "sample_sop.txt"
SAMPLE_CSV = Path(__file__).parent / "sample_data" / "sample_bank.csv"


def _save_upload(uploaded) -> str:
    suffix = Path(uploaded.name).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(uploaded.getbuffer())
    tmp.close()
    return tmp.name


def run_pipeline(sop_path, csv_path, payroll_path=None, pnl_path=None,
                 logistics_path=None):
    import pandas as pd
    steps = parse_sop(sop_path) if sop_path else []
    frames = [normalize_ledger(csv_path)]
    payroll = pd.DataFrame()
    if payroll_path:
        payroll = normalize_payroll(payroll_path)
        frames.append(payroll)
    if logistics_path:
        frames.append(normalize_logistics(logistics_path))
    events = pd.concat(frames, ignore_index=True).sort_values("timestamp")
    pnl = normalize_pnl(pnl_path) if pnl_path else pd.DataFrame()
    analysis = analyze(steps, events, payroll=payroll, pnl=pnl)
    tasks = generate_tasks(analysis, steps)
    return steps, events, analysis, tasks


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
col1, col2 = st.columns(2)
with col1:
    sop_file = st.file_uploader("SOP document (.pdf / .txt / .md) — optional",
                                type=["pdf", "txt", "md"])
    payroll_file = st.file_uploader("Payroll CSV — optional", type=["csv"])
with col2:
    csv_file = st.file_uploader("Bank / ledger CSV — required", type=["csv"])
    pnl_file = st.file_uploader("P&L export CSV — optional", type=["csv"])
logistics_file = st.file_uploader("Logistics / shipments CSV — optional",
                                  type=["csv"])

use_sample = st.button("Or load the sample dataset")
run = st.button("Run analysis", type="primary")

SAMPLE_PAYROLL = Path(__file__).parent / "sample_data" / "sample_payroll.csv"
SAMPLE_PNL = Path(__file__).parent / "sample_data" / "sample_pnl.csv"
SAMPLE_LOGISTICS = Path(__file__).parent / "sample_data" / "sample_logistics.csv"

if use_sample:
    st.session_state["steps"], st.session_state["events"], \
        st.session_state["analysis"], st.session_state["tasks"] = run_pipeline(
            str(SAMPLE_SOP), str(SAMPLE_CSV), str(SAMPLE_PAYROLL),
            str(SAMPLE_PNL), str(SAMPLE_LOGISTICS))
    st.session_state["sop_less"] = False
elif run and csv_file:
    sop_path = _save_upload(sop_file) if sop_file else None
    st.session_state["steps"], st.session_state["events"], \
        st.session_state["analysis"], st.session_state["tasks"] = run_pipeline(
            sop_path, _save_upload(csv_file),
            _save_upload(payroll_file) if payroll_file else None,
            _save_upload(pnl_file) if pnl_file else None,
            _save_upload(logistics_file) if logistics_file else None)
    st.session_state["sop_less"] = sop_path is None
elif run and not csv_file:
    st.warning("Upload a bank CSV first — that's the minimum input.")

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
if "analysis" not in st.session_state:
    st.info("Upload your files (or load the sample data) and run the analysis.")
    st.stop()

steps = st.session_state["steps"]
events = st.session_state["events"]
analysis = st.session_state["analysis"]
s = analysis["summary"]

if st.session_state.get("sop_less"):
    st.warning("SOP-less mode — process inferred from transaction history alone.")

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Unmapped leakage / yr", f"${s['annualized_leakage']:,.0f}")
m2.metric("SOP deviations", len(analysis["sop_deviations"]))
m3.metric("Threshold reviews", len(analysis["threshold_reviews"]))
m4.metric("Cycle-time gaps", len(analysis["cycle_gaps"]))
m5.metric("Payroll gaps", len(analysis.get("payroll_gaps", [])))
m6.metric("P&L flags", len(analysis.get("pnl_variance", [])))

tab_find, tab_map, tab_tasks, tab_agent = st.tabs(
    ["Findings", "Reality Map", "Task Queue", "Agent Workflow"])

with tab_find:
    leak = analysis["unmapped_leakage"]
    st.subheader(f"Unmapped leakage — ${s['monthly_leakage']:,.2f}/mo")
    if leak:
        st.dataframe(pd.DataFrame([{
            "Vendor": f["vendor_display"], "Cadence": f["cadence"],
            "Txns": f["transactions"], "$/mo": f["monthly_cost"],
            "$/yr": f["annualized"], "Category": f["category"],
        } for f in leak]), use_container_width=True)
    else:
        st.success("Every recurring vendor maps to an SOP step.")

    for title, items, key in [
        ("SOP deviations", analysis["sop_deviations"], None),
        ("Threshold reviews", analysis["threshold_reviews"], None),
        ("Cycle-time gaps", analysis["cycle_gaps"], None),
    ]:
        st.subheader(title)
        if items:
            for f in items:
                st.write(f"- {f['detail']}")
        else:
            st.success("None found.")

    if s.get("payroll_provided") or analysis.get("payroll_gaps"):
        st.subheader("Payroll reconciliation")
        pg = analysis.get("payroll_gaps", [])
        if pg:
            st.dataframe(pd.DataFrame([{
                "Direction": "Record w/o payment"
                if f["direction"] == "missing_payment"
                else "Payment w/o record",
                "Payee": f["payee"], "Amount": f["amount"],
                "Date": f["date"],
            } for f in pg]), use_container_width=True)
        else:
            st.success("Every payroll record matches a bank payment.")

    if s.get("pnl_provided") or analysis.get("pnl_variance"):
        st.subheader("P&L variance — books vs. bank")
        pv = analysis.get("pnl_variance", [])
        if pv:
            st.dataframe(pd.DataFrame([{
                "Account": f["account"], "Period": f["period"],
                "Books": f["pnl_total"], "Bank": f["bank_total"],
                "Variance %": f["variance_pct"],
            } for f in pv]), use_container_width=True)
        else:
            st.success("P&L totals match bank activity within tolerance.")


def _reality_map_dot() -> str:
    """DOT graph: SOP steps (blue) -> matched vendors (green);
    unmatched recurring vendors in red."""
    lines = ['digraph {', '  rankdir=LR;',
             '  node [shape=box, style="rounded,filled", fontsize=10];']
    matched_vendors = set()
    for stp in steps:
        label = f"{stp['step_id']}\n{(stp.get('expected_vendor') or '')[:24]}"
        lines.append(f'  "{label}" [fillcolor=lightblue1];')
        mv = _match_step_to_vendors(stp, events)
        for v in mv["vendor_payee"].unique():
            matched_vendors.add(v)
            vlabel = f"{v[:28]}"
            lines.append(f'  "{vlabel}" [fillcolor=palegreen];')
            lines.append(f'  "{label}" -> "{vlabel}";')
    for v, g in events.groupby("vendor_payee"):
        if v in matched_vendors or g["frequency"].iloc[0] == "ad-hoc":
            continue
        vlabel = f"{v[:28]}"
        cost = g["amount"].median() * (30.0 / max(
            float(g["timestamp"].sort_values().drop_duplicates()
                  .diff().dt.days.median()), 1))
        lines.append(
            f'  "{vlabel}" [fillcolor=indianred1, fontcolor=white, '
            f'label="{v[:22]}\\n~${cost:,.0f}/mo UNMAPPED"];')
    lines.append("}")
    return "\n".join(lines)


with tab_map:
    st.subheader("Visual process reality map")
    st.caption("Blue = documented SOP steps · Green = matched spend · "
               "Red = recurring spend with no SOP behind it")
    if steps:
        st.graphviz_chart(_reality_map_dot(), use_container_width=True)
    else:
        st.info("Upload an SOP to render the step-to-spend map.")

with tab_tasks:
    st.subheader("Task queue — exceptions routed to owners")
    st.caption("Every finding becomes a task for the role that owns it. "
               "This is what replaces the status meeting.")
    tasks = st.session_state.get("tasks", [])
    if not tasks:
        st.success("Queue is clear — no open exceptions.")
    else:
        open_tasks = [t for t in tasks if t["status"] != "resolved"]
        st.write(f"**{len(open_tasks)} open** / {len(tasks)} total")
        df = pd.DataFrame([{
            "ID": t["task_id"], "Priority": t["priority"],
            "Task": t["title"], "Owner": t["assignee"],
            "First action": t["first_action"], "Status": t["status"],
        } for t in tasks])
        edited = st.data_editor(
            df, use_container_width=True, hide_index=True,
            column_config={
                "Status": st.column_config.SelectboxColumn(
                    "Status", options=list(STATUSES), required=True),
            },
            disabled=["ID", "Priority", "Task", "Owner", "First action"],
            key="task_editor",
        )
        # Persist status changes back to session state.
        for _, row in edited.iterrows():
            for t in tasks:
                if t["task_id"] == row["ID"]:
                    t["status"] = row["Status"]
        st.session_state["tasks"] = tasks

with tab_agent:
    st.subheader("Deployable agent workflow")
    st.caption("Generates a runnable LangGraph workflow from your reconciled "
               "SOP steps — Pydantic schemas, approval routing, "
               "human-in-the-loop checkpoints.")
    if not steps:
        st.info("Upload an SOP first — the workflow is generated from "
                "documented steps.")
    elif st.button("Generate workflow.py"):
        with tempfile.NamedTemporaryFile(delete=False,
                                         suffix=".py") as tmp:
            out = tmp.name
        generate_workflow(steps, out_path=out)
        code = Path(out).read_text()
        st.session_state["workflow_code"] = code
    if "workflow_code" in st.session_state:
        st.code(st.session_state["workflow_code"], language="python")
        st.download_button("Download workflow.py",
                           st.session_state["workflow_code"],
                           file_name="workflow.py")
