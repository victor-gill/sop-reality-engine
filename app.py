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
from config import load_config, save_config, threshold_for

HERE = Path(__file__).parent
STATE_DIR = HERE / "state"

st.set_page_config(page_title="SOP Reality Engine", layout="wide")
st.title("SOP Reality Engine")
st.caption("What your SOPs say vs. what your money does — "
           "the gap is where profit leaks.")

# --- Tuesday-morning digest (written by a local runner.py run) -----------------
_digest_path = STATE_DIR / "digest.json"
if _digest_path.exists():
    import json
    _digest = json.loads(_digest_path.read_text())
    st.info(f"**Latest scheduled run** ({_digest['run_at'][:10]}, "
            f"from your local runner): {_digest['headline']}")

def _dataframe(data, **kw):
    """st.dataframe with full width — new API when available, fallback."""
    try:
        return st.dataframe(data, width="stretch", **kw)
    except TypeError:
        return st.dataframe(data, use_container_width=True, **kw)


def _graphviz(dot: str):
    try:
        return st.graphviz_chart(dot, width="stretch")
    except TypeError:
        return st.graphviz_chart(dot, use_container_width=True)


SAMPLE_SOP = Path(__file__).parent / "sample_data" / "sample_sop.txt"
SAMPLE_CSV = Path(__file__).parent / "sample_data" / "sample_bank.csv"
SAMPLE_PAYROLL = Path(__file__).parent / "sample_data" / "sample_payroll.csv"
SAMPLE_PNL = Path(__file__).parent / "sample_data" / "sample_pnl.csv"
SAMPLE_LOGISTICS = Path(__file__).parent / "sample_data" / "sample_logistics.csv"

# --- Per-session isolation ---------------------------------------------------
# Every visitor gets their own working directory: uploads, generated
# workflow, approvals, and settings never touch another session or the
# shared ./state directory (which the local runner owns).
@st.cache_resource
def _sweep_old_sessions():
    """Delete session dirs older than 4h. Streamlit has no reliable
    session-end hook, so stale vendor names/amounts would otherwise
    accumulate in /tmp forever."""
    import shutil
    import time
    tmp = Path(tempfile.gettempdir())
    now = time.time()
    for d in tmp.glob("sopre_session_*"):
        try:
            if now - d.stat().st_mtime > 4 * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
    return True


_sweep_old_sessions()


@st.cache_resource
def _llm_budget():
    """Process-wide LLM spend cap. Per-session counters reset on refresh;
    this one can't be bypassed that way. (On multi-replica hosting each
    replica gets its own budget — still strictly better.)"""
    import threading
    return {"used": 0, "lock": threading.Lock()}


MAX_LLM_PARSES_GLOBAL = 20


def _session_dir() -> Path:
    if "session_dir" not in st.session_state:
        d = Path(tempfile.mkdtemp(prefix="sopre_session_"))
        st.session_state["session_dir"] = str(d)
        import shutil
        shutil.copy(HERE / "business_config.yaml",
                    d / "business_config.yaml")
    return Path(st.session_state["session_dir"])


def _session_config_path() -> str:
    return str(_session_dir() / "business_config.yaml")


def _dismissed_vendors() -> set:
    p = _session_dir() / "dismissed.json"
    if p.exists():
        import json
        return set(json.loads(p.read_text()))
    return set()


def _save_upload(uploaded, session_dir: Path) -> str:
    """Write an upload into the session dir. Caller must delete it.

    If the write itself fails, the partial file is removed here so a
    failed upload can never leak into tmp_paths-less limbo.
    """
    suffix = Path(uploaded.name).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix,
                                      dir=str(session_dir))
    try:
        tmp.write(uploaded.getbuffer())
        tmp.close()
    except Exception:
        try:
            tmp.close()
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        raise
    return tmp.name


def run_pipeline(sop_path, csv_path, payroll_path=None, pnl_path=None,
                 logistics_path=None, dismissed=None, seed_demo=False):
    import hashlib
    import pandas as pd
    import sop_parser
    sdir = _session_dir()
    # Touch the session dir so the startup sweep doesn't reap active sessions.
    sdir.touch(exist_ok=True)
    budget = _llm_budget()
    with budget["lock"]:
        allow_llm = budget["used"] < MAX_LLM_PARSES_GLOBAL
    steps = parse_sop(sop_path, allow_llm=allow_llm) if sop_path else []
    if sop_parser.last_used_llm:
        with budget["lock"]:
            budget["used"] += 1
            _llm_used_now = budget["used"]
        st.caption(f"AI parsing used ({_llm_used_now}/{MAX_LLM_PARSES_GLOBAL} "
                   f"this session window).")
    frames = [normalize_ledger(csv_path)]
    payroll = pd.DataFrame()
    if payroll_path:
        payroll = normalize_payroll(payroll_path)
        frames.append(payroll)
    if logistics_path:
        frames.append(normalize_logistics(logistics_path))
    events = pd.concat(frames, ignore_index=True).sort_values("timestamp")
    pnl = normalize_pnl(pnl_path) if pnl_path else pd.DataFrame()
    # Business-vs-personal classification. Shares the process-wide LLM
    # budget with SOP parsing; deterministic + heuristic layers always
    # run, the LLM only reviews the ambiguous remainder.
    import expense_classifier
    from expense_classifier import classify_expenses
    with budget["lock"]:
        clf_allow = budget["used"] < MAX_LLM_PARSES_GLOBAL
    _cfg = load_config(_session_config_path())
    events = classify_expenses(events, _cfg, allow_llm=clf_allow)
    if expense_classifier.last_llm_calls:
        with budget["lock"]:
            budget["used"] += expense_classifier.last_llm_calls
    analysis = analyze(steps, events, payroll=payroll, pnl=pnl)
    if dismissed:
        analysis["unmapped_leakage"] = [
            f for f in analysis["unmapped_leakage"]
            if f["vendor"] not in dismissed]
    tasks = generate_tasks(analysis, steps)

    # Generate the agent workflow into the session dir so the Approvals
    # tab can execute decisions against this exact run.
    if steps:
        import importlib.util
        wf_path = sdir / "workflow.py"
        generate_workflow(steps, out_path=str(wf_path))
        st.session_state["workflow_path"] = str(wf_path)
        # Hash at generation time; verified before any execution.
        st.session_state["workflow_hash"] = hashlib.sha256(
            wf_path.read_bytes()).hexdigest()
        if seed_demo:
            # Seed one demo approval so the loop is visible end to end.
            wf = _load_workflow_module()
            wf.run_transaction("Acme Industrial Supply", 6800.00,
                               "demo-seed-1", str(sdir / "approvals"),
                               memo="seeded demo approval")
    return steps, events, analysis, tasks


def _load_workflow_module():
    """Import the session's generated workflow after verifying integrity.

    Three checks before executing: the path is inside this session's dir
    (never reachable from user input), the content hash matches what was
    generated moments ago in this session, and the file carries the
    generator's marker header.
    """
    import hashlib
    import importlib.util
    wf_path = Path(st.session_state.get("workflow_path", ""))
    expected_dir = _session_dir().resolve()
    if not wf_path.exists() or expected_dir not in wf_path.resolve().parents:
        raise FileNotFoundError("no generated workflow for this session")
    digest = hashlib.sha256(wf_path.read_bytes()).hexdigest()
    if digest != st.session_state.get("workflow_hash"):
        raise ValueError("workflow file changed since generation — "
                         "refusing to execute")
    head = wf_path.read_text(encoding="utf-8")[:500]
    if "Auto-generated agent workflow" not in head:
        raise ValueError("workflow file failed provenance check")
    import os
    os.environ["SOPRE_CONFIG"] = _session_config_path()
    spec = importlib.util.spec_from_file_location("sop_workflow",
                                                  str(wf_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
st.caption("Your files are processed in this session and deleted after the "
           "run — nothing is uploaded or stored anywhere.")
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

# Column-mapping preview: show the auto-detection before the run.
if csv_file:
    with st.expander("Column mapping preview — what the engine detected"):
        import pandas as pd
        from financial_normalizer import detect_ledger_columns
        _preview = pd.read_csv(csv_file, nrows=5)
        _mapping = detect_ledger_columns(_preview)
        for role, col in _mapping.items():
            st.write(f"- **{role}**: {col if col else '⚠️ not detected'}")
        if not all([_mapping["date"], _mapping["payee"],
                    _mapping["amount"]]):
            st.warning("The engine couldn't map a required column. "
                       "Rename your headers to something like "
                       "Date / Description / Amount and re-upload.")
        csv_file.seek(0)

use_sample = st.button("Or load the sample dataset")
run = st.button("Run analysis", type="primary")

if use_sample:
    st.session_state["steps"], st.session_state["events"], \
        st.session_state["analysis"], st.session_state["tasks"] = run_pipeline(
            str(SAMPLE_SOP), str(SAMPLE_CSV), str(SAMPLE_PAYROLL),
            str(SAMPLE_PNL), str(SAMPLE_LOGISTICS), seed_demo=True)
    st.session_state["sop_less"] = False
elif run and csv_file:
    sdir = _session_dir()
    tmp_paths = []
    try:
        sop_path = None
        if sop_file:
            sop_path = _save_upload(sop_file, sdir)
            tmp_paths.append(sop_path)
        bank_path = _save_upload(csv_file, sdir)
        tmp_paths.append(bank_path)
        payroll_path = pnl_path = logistics_path = None
        for up, slot in [(payroll_file, "payroll"), (pnl_file, "pnl"),
                         (logistics_file, "logistics")]:
            if up:
                p = _save_upload(up, sdir)
                tmp_paths.append(p)
                if slot == "payroll":
                    payroll_path = p
                elif slot == "pnl":
                    pnl_path = p
                else:
                    logistics_path = p
        st.session_state["steps"], st.session_state["events"], \
            st.session_state["analysis"], st.session_state["tasks"] = run_pipeline(
                sop_path, bank_path, payroll_path, pnl_path, logistics_path,
                dismissed=_dismissed_vendors())
        st.session_state["sop_less"] = sop_path is None
    finally:
        for p in tmp_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
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

tab_find, tab_map, tab_tasks, tab_agent, tab_approvals, tab_settings = st.tabs(
    ["Findings", "Reality Map", "Task Queue", "Agent Workflow",
     "Approvals", "Settings"])

with tab_find:
    leak = analysis["unmapped_leakage"]
    st.subheader(f"Unmapped leakage — ${s['monthly_leakage']:,.2f}/mo")
    st.caption("Cadence inference can flag annual renewals or seasonal "
               "vendors. If a charge is legitimate, mark it expected — "
               "it stays dismissed for this session.")
    if leak:
        _dataframe(pd.DataFrame([{
            "Vendor": f["vendor_display"], "Cadence": f["cadence"],
            "Txns": f["transactions"], "$/mo": f["monthly_cost"],
            "$/yr": f["annualized"], "Category": f["category"],
        } for f in leak]))
        _dismiss_choice = st.selectbox(
            "Mark a vendor as expected (dismiss the finding)",
            ["—"] + [f["vendor_display"] for f in leak],
            key="dismiss_vendor")
        if _dismiss_choice != "—" and st.button("Dismiss",
                                               key="dismiss_btn"):
            import json as _djson
            _match = next(f for f in leak
                          if f["vendor_display"] == _dismiss_choice)
            _dismissed = _dismissed_vendors()
            _dismissed.add(_match["vendor"])
            (_session_dir() / "dismissed.json").write_text(
                _djson.dumps(sorted(_dismissed)))
            # Remove from the live findings and their tasks.
            analysis["unmapped_leakage"] = [
                f for f in leak
                if f["vendor"] not in _dismissed]
            st.session_state["analysis"] = analysis
            st.session_state["tasks"] = [
                t for t in st.session_state.get("tasks", [])
                if not (t["type"] == "unmapped_leakage" and
                        _match["vendor_display"] in t["title"])]
            st.rerun()
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
            _dataframe(pd.DataFrame([{
                "Direction": "Record w/o payment"
                if f["direction"] == "missing_payment"
                else "Payment w/o record",
                "Payee": f["payee"], "Amount": f["amount"],
                "Date": f["date"],
            } for f in pg]))
        else:
            st.success("Every payroll record matches a bank payment.")

    if s.get("pnl_provided") or analysis.get("pnl_variance"):
        st.subheader("P&L variance — books vs. bank")
        pv = analysis.get("pnl_variance", [])
        if pv:
            _dataframe(pd.DataFrame([{
                "Account": f["account"], "Period": f["period"],
                "Books": f["pnl_total"], "Bank": f["bank_total"],
                "Variance %": f["variance_pct"],
            } for f in pv]))
        else:
            st.success("P&L totals match bank activity within tolerance.")


def _dot_escape(text: str) -> str:
    """Escape a label for DOT: quotes/backslashes break the graph."""
    return str(text).replace(chr(92), chr(92)*2).replace('"', chr(92)+'"')[:28]


def _reality_map_dot() -> str:
    """DOT graph: SOP steps (blue) -> matched vendors (green);
    unmatched recurring vendors in red."""
    lines = ['digraph {', '  rankdir=LR;',
             '  node [shape=box, style="rounded,filled", fontsize=10];']
    matched_vendors = set()
    for stp in steps:
        label = (_dot_escape(stp["step_id"]) + "\\n" +
                 _dot_escape((stp.get("expected_vendor") or "")[:24]))
        lines.append(f'  "{label}" [fillcolor=lightblue1];')
        mv = _match_step_to_vendors(stp, events)
        for v in mv["vendor_payee"].unique():
            matched_vendors.add(v)
            vlabel = _dot_escape(v)
            lines.append(f'  "{vlabel}" [fillcolor=palegreen];')
            lines.append(f'  "{label}" -> "{vlabel}";')
    for v, g in events.groupby("vendor_payee"):
        if v in matched_vendors or g["frequency"].iloc[0] == "ad-hoc":
            continue
            vlabel = _dot_escape(v)
        cost = g["amount"].median() * (30.0 / max(
            float(g["timestamp"].sort_values().drop_duplicates()
                  .diff().dt.days.median()), 1))
        lines.append(
            f'  "{vlabel}" [fillcolor=indianred1, fontcolor=white, '
            f'label="{_dot_escape(v[:22])}\\n~${cost:,.0f}/mo UNMAPPED"];')
    lines.append("}")
    return "\n".join(lines)


with tab_map:
    st.subheader("Visual process reality map")
    st.caption("Blue = documented SOP steps · Green = matched spend · "
               "Red = recurring spend with no SOP behind it")
    if steps:
        _graphviz(_reality_map_dot())
    else:
        st.info("Upload an SOP to render the step-to-spend map.")

with tab_tasks:
    st.subheader("Task queue — exceptions routed to owners")
    st.caption("Every finding becomes a task for the role that owns it. "
               "This is what replaces the status meeting.")
    import json as _tjson
    _tasks_path = _session_dir() / "tasks.json"
    tasks = st.session_state.get("tasks")
    if tasks is None and _tasks_path.exists():
        tasks = _tjson.loads(_tasks_path.read_text())
        st.session_state["tasks"] = tasks
    tasks = tasks or []
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
            df, hide_index=True,
            column_config={
                "Status": st.column_config.SelectboxColumn(
                    "Status", options=list(STATUSES), required=True),
            },
            disabled=["ID", "Priority", "Task", "Owner", "First action"],
            key="task_editor",
        )
        # Persist status changes to the session dir — survives refresh.
        changed = False
        for _, row in edited.iterrows():
            for t in tasks:
                if t["task_id"] == row["ID"] and \
                        t["status"] != row["Status"]:
                    t["status"] = row["Status"]
                    changed = True
        if changed or not _tasks_path.exists():
            st.session_state["tasks"] = tasks
            _tasks_path.write_text(_tjson.dumps(tasks, indent=2))

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

# ---------------------------------------------------------------------------
# Approvals — the owner experience: pending decisions as taps.
# ---------------------------------------------------------------------------
with tab_approvals:
    st.subheader("Pending approvals")
    st.caption("Payments the agent held for a human. Approve or reject — "
               "the workflow completes itself. Decisions are handled by a "
               "fixed module, never by executing generated code.")
    from approvals import (pending_requests, decided_requests,
                           resolve_approval)

    def _approval_rows(appr_dir: Path, label: str):
        """Render one approval source (session or local runner)."""
        pending = pending_requests(appr_dir)
        if label:
            st.markdown(f"**{label}**")
        if not pending:
            st.success("Nothing waiting on you here.")
            return
        for req in pending:
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            c1.write(f"**{req['vendor_name']}** — ${req['amount']:,.2f}")
            c2.write(f"{req.get('category', '')} · "
                     f"threshold ${req.get('threshold', 0):,.0f}")
            key = f"{label}_{req['txn_id']}" if label else req['txn_id']
            if c3.button("Approve", key=f"ap_{key}"):
                try:
                    resolve_approval(appr_dir, req["txn_id"], "approved",
                                     decided_by="app owner")
                except (FileNotFoundError, ValueError) as e:
                    st.error(str(e))
                st.rerun()
            if c4.button("Reject", key=f"rj_{key}"):
                try:
                    resolve_approval(appr_dir, req["txn_id"], "rejected",
                                     decided_by="app owner")
                except (FileNotFoundError, ValueError) as e:
                    st.error(str(e))
                st.rerun()

    _approval_rows(_session_dir() / "approvals", "")

    # Bridge to the scheduled loop: if a local runner.py wrote approvals
    # into ./state, surface them here too (labeled). On the hosted demo
    # ./state doesn't exist, so sessions stay isolated.
    _local_appr = HERE / "state" / "approvals"
    if pending_requests(_local_appr):
        _approval_rows(_local_appr, "From your local runner")

    _decided = decided_requests(_session_dir() / "approvals")
    if _decided:
        st.subheader("Decided")
        _dataframe([{"Vendor": r["vendor_name"], "Amount": r["amount"],
                     "Decision": r["status"], "By": r["decided_by"]}
                    for r in _decided])

# ---------------------------------------------------------------------------
# Settings — the customizable layer, no code. Session-scoped.
# ---------------------------------------------------------------------------
with tab_settings:
    st.subheader("Business settings")
    st.caption("Change the rules here — thresholds, vendors, your team. "
               "The audit, the tasks, and the agent all follow on the "
               "next run. No code changes. Changes apply to this session "
               "only.")
    cfg = load_config(_session_config_path())

    cfg["business_name"] = st.text_input("Business name",
                                        cfg.get("business_name", ""))
    cfg["approval_threshold"] = st.number_input(
        "Approval threshold ($) — anything above needs a human",
        min_value=0, value=int(cfg.get("approval_threshold", 5000)), step=100)

    st.markdown("**Per-category thresholds** (override the global one)")
    _cat_lines = "\n".join(f"{k}: {v}" for k, v in
                           (cfg.get("category_thresholds") or {}).items())
    _cat_text = st.text_area("One per line, e.g. `Payroll: 10000`",
                             _cat_lines, height=80)
    _cat = {}
    for line in _cat_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                _cat[k.strip()] = float(v.strip())
            except ValueError:
                pass
    cfg["category_thresholds"] = _cat

    st.markdown("**Vendors**")
    _ap = st.text_area("Auto-pay vendors (one per line)",
                       "\n".join(cfg.get("auto_pay_vendors", [])),
                       height=100)
    cfg["auto_pay_vendors"] = [v.strip() for v in _ap.splitlines() if v.strip()]
    _ar = st.text_area("Always-review vendors (one per line)",
                       "\n".join(cfg.get("always_review_vendors", [])),
                       height=70)
    cfg["always_review_vendors"] = [v.strip() for v in _ar.splitlines()
                                    if v.strip()]

    st.markdown("**Team** — who owns each role")
    _roles = cfg.get("roles") or {}
    for role in ["Operator", "Compliance Lead", "Fleet Coordinator",
                 "Tech Lead", "Accounts Payable Clerk", "Payroll Lead",
                 "Controller"]:
        _roles[role] = st.text_input(role, _roles.get(role, ""),
                                     key=f"role_{role}")
    cfg["roles"] = _roles

    cfg["digest_cadence"] = st.selectbox(
        "Digest cadence", ["weekly", "daily"],
        index=0 if cfg.get("digest_cadence", "weekly") == "weekly" else 1)

    if st.button("Save settings", type="primary"):
        try:
            save_config(cfg, _session_config_path())
            st.success("Saved for this session. The next run follows the new "
                       "rules.")
        except ValueError as e:
            st.error(f"Couldn't save settings: {e}")

    st.markdown("**Carry your settings** — a refresh starts a new session, "
                "so export the config to keep it (one file per client).")
    _cfg_text = Path(_session_config_path()).read_text(encoding="utf-8")
    st.download_button("Export config (YAML)", _cfg_text,
                       file_name="business_config.yaml", key="cfg_export")
    _cfg_up = st.file_uploader("Import config", type=["yaml", "yml"],
                               key="cfg_import")
    if _cfg_up is not None and st.button("Import", key="cfg_import_btn"):
        import yaml
        from config import MAX_CONFIG_BYTES
        try:
            if _cfg_up.size > MAX_CONFIG_BYTES:
                raise ValueError(f"file exceeds {MAX_CONFIG_BYTES} bytes")
            _incoming = yaml.safe_load(_cfg_up.read()) or {}
            _known = {"business_name", "approval_threshold",
                      "category_thresholds", "auto_pay_vendors",
                      "always_review_vendors", "roles", "digest_cadence",
                      "digest_min_leakage", "auxiliary_categories",
                      "transfer_categories"}
            _clean = {k: v for k, v in _incoming.items() if k in _known}
            _merged = load_config(_session_config_path())
            _merged.update(_clean)
            save_config(_merged, _session_config_path())
            st.success(f"Imported {_cfg_up.name} — settings applied.")
            st.rerun()
        except Exception as e:
            st.error(f"Couldn't import that file: {e}")
