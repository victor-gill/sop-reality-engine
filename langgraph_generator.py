"""
LangGraph Generator — Phase 2 of the pipeline.

Takes validated SOP steps (the reconciled "true workflow") and generates a
runnable LangGraph agent workflow: Pydantic schemas per step, state nodes,
and conditional edges derived from SOP approval thresholds.

Usage:
    from langgraph_generator import generate_workflow
    path = generate_workflow(steps, out_path="workflow.py")

The generated workflow.py is self-contained and executable — it includes
stub agent functions with real routing logic and a __main__ demo.
"""

import re


def _class_name(step_id: str) -> str:
    """'STEP_01' -> 'Step01' (valid Python identifier)."""
    return re.sub(r"\W", "", step_id.title().replace("_", ""))


def _py_str(value) -> str:
    """Render a Python string literal safely."""
    return repr(str(value))


def _safe_doc(text: str, limit: int = 60) -> str:
    """Make arbitrary SOP text safe for interpolation into generated
    Python (docstrings). A crafted SOP must never break out of the
    string context: strip triple quotes, backslashes, and newlines."""
    t = re.sub(r"\s+", " ", str(text))[:limit]
    return t.replace("\\", "/").replace('"""', "'''").replace('"', "'")


def _safe_threshold(value) -> float:
    """Coerce an approval threshold to float; never interpolate raw."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 5000.0


def generate_workflow(steps: list[dict], out_path: str = "workflow.py") -> str:
    """Generate a runnable LangGraph workflow from SOP steps."""
    financial_steps = [s for s in steps if s.get("expected_amount") is not None]
    thresholds = [_safe_threshold(s["approval_threshold"]) for s in steps
                  if s.get("approval_threshold")]
    approval_threshold = max(thresholds) if thresholds else 5000.0
    vendors = sorted({s.get("expected_vendor", "")
                      for s in steps if s.get("expected_vendor")})

    # --- Pydantic schemas, one per cost-bearing step ---------------------
    schema_blocks = []
    for s in financial_steps:
        cn = _class_name(s["step_id"])
        schema_blocks.append(f'''\
class {cn}Schema(BaseModel):
    """Validates '{_safe_doc(s.get('action', ''))}' ({_safe_doc(s['step_id'], 20)})."""
    vendor_name: str = Field(..., description="Counterparty name")
    amount: float = Field(..., gt=0, description="Transaction amount")
    requires_approval: bool = Field(
        default=False,
        description="True when amount exceeds the SOP approval threshold",
    )

    @field_validator("requires_approval", mode="before")
    @classmethod
    def _check_threshold(cls, v, info):
        amount = (info.data or {{}}).get("amount", 0)
        threshold = _load_rules().get("approval_threshold", 5000)
        return bool(amount and amount > threshold)
''')
    schemas_code = "\n".join(schema_blocks) if schema_blocks else (
        'class GenericStepSchema(BaseModel):\n'
        '    vendor_name: str = Field(..., description="Counterparty name")\n'
        '    amount: float = Field(..., gt=0, description="Transaction amount")\n'
    )

    code = f'''\
"""
Auto-generated agent workflow — SOP Reality Engine, Phase 2.

Derived from reconciled SOP steps. Nodes enforce the documented process;
conditional edges encode the SOP approval thresholds. Stub agent functions
contain real routing logic — swap in LLM-backed agents for production.
"""

from typing import Optional, TypedDict
from pathlib import Path

from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Configuration distilled from the SOP
# ---------------------------------------------------------------------------
# Baked-in defaults from generation time. At runtime the workflow
# re-reads business_config.yaml (or $SOPRE_CONFIG), so the owner can
# change thresholds, vendors, and rules without regenerating.
_BAKED_RULES = {{
    "approval_threshold": {approval_threshold},
    "category_thresholds": {{}},
    "auto_pay_vendors": {vendors!r},
    "always_review_vendors": [],
}}


def _load_rules():
    import os
    rules = dict(_BAKED_RULES)
    cfg_path = os.environ.get(
        "SOPRE_CONFIG",
        str(Path(__file__).parent / "business_config.yaml"),
    )
    try:
        import yaml
        with open(cfg_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {{}}
        for key in rules:
            if loaded.get(key) is not None:
                rules[key] = loaded[key]
    except Exception:
        pass  # fall back to baked rules
    return rules


RULES = _load_rules()
# Backwards-compatible aliases
APPROVAL_THRESHOLD = RULES["approval_threshold"]
APPROVED_VENDORS = RULES["auto_pay_vendors"]

# ---------------------------------------------------------------------------
# Pydantic schemas (one per cost-bearing SOP step)
# ---------------------------------------------------------------------------
{schemas_code}
class WorkflowInput(BaseModel):
    vendor_name: str
    amount: float = Field(..., gt=0)
    memo: str = ""


# ---------------------------------------------------------------------------
# Workflow state
# ---------------------------------------------------------------------------
class OperationalState(TypedDict):
    txn_id: str
    approvals_dir: str
    vendor_name: str
    amount: float
    memo: str
    category: str
    approved_vendor: bool
    requires_approval: bool
    approved: Optional[bool]
    outcome: str
    log: list


# ---------------------------------------------------------------------------
# Agent nodes
# ---------------------------------------------------------------------------
def intake_node(state: OperationalState) -> OperationalState:
    """Validate input shape and check the vendor against the SOP list."""
    parsed = WorkflowInput(
        vendor_name=state["vendor_name"],
        amount=state["amount"],
        memo=state.get("memo", ""),
    )
    state["log"].append(f"intake: {{parsed.vendor_name}} ${{parsed.amount:,.2f}}")
    state["approved_vendor"] = any(
        v.lower() in parsed.vendor_name.lower() for v in APPROVED_VENDORS
    )
    if not state["approved_vendor"]:
        state["log"].append("intake: vendor NOT in SOP list — flagged as unmapped")
    return state


def validate_node(state: OperationalState) -> OperationalState:
    """Apply the SOP approval threshold (customizable per category)."""
    category = state.get("category") or ""
    threshold = RULES["approval_threshold"]
    override = (RULES.get("category_thresholds") or {{}}).get(category)
    if override is not None:
        threshold = override
    state["requires_approval"] = state["amount"] > threshold
    if any(v.lower() in state["vendor_name"].lower()
           for v in RULES.get("always_review_vendors", [])):
        state["requires_approval"] = True
        state["log"].append("validate: vendor is on the always-review list")
    state["log"].append(
        f"validate: ${{state['amount']:,.2f}} "
        f"{{'exceeds' if state['requires_approval'] else 'within'}} "
        f"threshold (${{threshold:,.0f}})"
    )
    return state


def route_approval(state: OperationalState) -> str:
    """Conditional edge: over threshold -> human, else auto-pay."""
    if state["requires_approval"]:
        return "human_approval"
    return "auto_pay"


def human_approval_node(state: OperationalState) -> OperationalState:
    """Human-in-the-loop checkpoint.

    Production behavior: writes an approval request to
    ``{{approvals_dir}}/pending/{{txn_id}}.json`` and pauses. The owner
    approves/rejects from the app (Approvals tab); ``resolve_approval``
    completes the workflow. No terminal prompt — approvals are taps.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    pending_dir = Path(state["approvals_dir"]) / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    request = {{
        "txn_id": state["txn_id"],
        "vendor_name": state["vendor_name"],
        "amount": state["amount"],
        "memo": state.get("memo", ""),
        "category": state.get("category", ""),
        "approved_vendor": state["approved_vendor"],
        "threshold": RULES["approval_threshold"],
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }}
    (pending_dir / f"{{state['txn_id']}}.json").write_text(
        json.dumps(request, indent=2))
    state["approved"] = None
    state["outcome"] = "PENDING_APPROVAL"
    state["log"].append(
        f"human_approval: request written for {{state['txn_id']}} — "
        f"awaiting owner decision in the app")
    return state


def auto_pay_node(state: OperationalState) -> OperationalState:
    """Execution stub: marks the payment released."""
    state["approved"] = True
    state["outcome"] = "AUTO_PAID"
    state["log"].append("auto_pay: payment released")
    return state


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
def build_workflow():
    builder = StateGraph(OperationalState)
    builder.add_node("intake", intake_node)
    builder.add_node("validate", validate_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_node("auto_pay", auto_pay_node)
    builder.set_entry_point("intake")
    builder.add_edge("intake", "validate")
    builder.add_conditional_edges("validate", route_approval)
    builder.add_edge("human_approval", END)
    builder.add_edge("auto_pay", END)
    return builder.compile()


# ---------------------------------------------------------------------------
# Runner API — used by the scheduled runner and the app
# ---------------------------------------------------------------------------
def run_transaction(vendor_name: str, amount: float, txn_id: str,
                    approvals_dir: str, memo: str = "",
                    category: str = "") -> dict:
    """Run one transaction through the workflow.

    Returns the final state. If the outcome is PENDING_APPROVAL, an
    approval request was written to ``{{approvals_dir}}/pending/`` — the
    owner decides in the app, then ``resolve_approval`` completes it.
    """
    workflow = build_workflow()
    return workflow.invoke({{
        "txn_id": txn_id,
        "approvals_dir": approvals_dir,
        "vendor_name": vendor_name,
        "amount": amount,
        "memo": memo,
        "category": category,
        "approved_vendor": False,
        "requires_approval": False,
        "approved": None,
        "outcome": "",
        "log": [],
    }})


def resolve_approval(approvals_dir: str, txn_id: str, decision: str,
                     decided_by: str = "owner") -> dict:
    """Complete a pending approval: decision is 'approved' or 'rejected'."""
    import json
    from datetime import datetime, timezone

    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    base = Path(approvals_dir)
    pending = base / "pending" / f"{{txn_id}}.json"
    if not pending.exists():
        raise FileNotFoundError(f"no pending approval for {{txn_id}}")
    request = json.loads(pending.read_text())
    decided_dir = base / "decided"
    decided_dir.mkdir(parents=True, exist_ok=True)
    record = {{
        **request,
        "status": decision,
        "decided_by": decided_by,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "outcome": "APPROVED_PAID" if decision == "approved" else "REJECTED",
    }}
    (decided_dir / f"{{txn_id}}.json").write_text(json.dumps(record, indent=2))
    pending.unlink()
    return record


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile
    approvals = tempfile.mkdtemp(prefix="approvals_demo_")
    pending_ids = []
    for i, (vendor, amount) in enumerate([
        ("Ledgible Inc.", 89.00),            # under threshold -> auto-pay
        ("Acme Industrial Supply", 6800.00),  # over threshold -> approval
        ("Unknown Vendor LLC", 250.00),      # unmapped vendor, under threshold
    ]):
        txn_id = f"demo-{{i+1}}"
        result = run_transaction(vendor, amount, txn_id, approvals,
                                 memo="demo")
        print(f"\\n{{vendor}} ${{amount:,.2f}} -> {{result['outcome']}}")
        for line in result["log"]:
            print(f"   {{line}}")
        if result["outcome"] == "PENDING_APPROVAL":
            pending_ids.append(txn_id)
    # Owner taps "approve" in the app — simulate the decision here.
    for txn_id in pending_ids:
        record = resolve_approval(approvals, txn_id, "approved",
                                  decided_by="demo owner")
        print(f"\\n{{txn_id}} decided: {{record['outcome']}} "
              f"(approved by {{record['decided_by']}})")
    print(f"\\nApproval requests were written to {{approvals}}/pending/ "
          f"and resolved into {{approvals}}/decided/")
'''
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[generator] Workflow written to {out_path} "
          f"({len(financial_steps)} step schemas, "
          f"threshold={approval_threshold})")
    return out_path
