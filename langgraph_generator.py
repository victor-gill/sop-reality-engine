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


def generate_workflow(steps: list[dict], out_path: str = "workflow.py") -> str:
    """Generate a runnable LangGraph workflow from SOP steps."""
    financial_steps = [s for s in steps if s.get("expected_amount") is not None]
    thresholds = [s["approval_threshold"] for s in steps
                  if s.get("approval_threshold")]
    approval_threshold = max(thresholds) if thresholds else None
    vendors = sorted({s.get("expected_vendor", "")
                      for s in steps if s.get("expected_vendor")})

    # --- Pydantic schemas, one per cost-bearing step ---------------------
    schema_blocks = []
    for s in financial_steps:
        cn = _class_name(s["step_id"])
        schema_blocks.append(f'''\
class {cn}Schema(BaseModel):
    """Validates '{s.get('action', '')[:60]}' ({s['step_id']})."""
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
        return bool(amount and amount > {approval_threshold or 'float("inf")'})
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

from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Configuration distilled from the SOP
# ---------------------------------------------------------------------------
APPROVAL_THRESHOLD = {approval_threshold if approval_threshold else 'None'}
APPROVED_VENDORS = {vendors!r}

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
    vendor_name: str
    amount: float
    memo: str
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
    """Apply the SOP approval threshold."""
    threshold = APPROVAL_THRESHOLD if APPROVAL_THRESHOLD else float("inf")
    state["requires_approval"] = state["amount"] > threshold
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
    """Human-in-the-loop checkpoint (stub: auto-flags for review)."""
    state["approved"] = None  # pending human decision in production
    state["outcome"] = "PENDING_APPROVAL"
    state["log"].append("human_approval: routed for manual review")
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
# Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    workflow = build_workflow()
    for vendor, amount in [
        ("Ledgible Inc.", 89.00),          # under threshold -> auto-pay
        ("Acme Industrial Supply", 6800.00),  # over threshold -> human
        ("Unknown Vendor LLC", 250.00),    # unmapped vendor, under threshold
    ]:
        result = workflow.invoke({{
            "vendor_name": vendor,
            "amount": amount,
            "memo": "demo",
            "approved_vendor": False,
            "requires_approval": False,
            "approved": None,
            "outcome": "",
            "log": [],
        }})
        print(f"\\n{{vendor}} ${{amount:,.2f}} -> {{result['outcome']}}")
        for line in result["log"]:
            print(f"   {{line}}")
'''
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[generator] Workflow written to {out_path} "
          f"({len(financial_steps)} step schemas, "
          f"threshold={approval_threshold})")
    return out_path
