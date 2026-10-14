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
APPROVAL_THRESHOLD = 5000.0
APPROVED_VENDORS = ['Ledgible', 'Northbridge Staffing', 'RouteFuel', 'SafeCert Auditing', 'Twilio API']

# ---------------------------------------------------------------------------
# Pydantic schemas (one per cost-bearing SOP step)
# ---------------------------------------------------------------------------
class Step01Schema(BaseModel):
    """Validates 'Pay Ledgible bookkeeping subscription' (STEP_01)."""
    vendor_name: str = Field(..., description="Counterparty name")
    amount: float = Field(..., gt=0, description="Transaction amount")
    requires_approval: bool = Field(
        default=False,
        description="True when amount exceeds the SOP approval threshold",
    )

    @field_validator("requires_approval", mode="before")
    @classmethod
    def _check_threshold(cls, v, info):
        amount = (info.data or {}).get("amount", 0)
        return bool(amount and amount > 5000.0)

class Step03Schema(BaseModel):
    """Validates 'Pay SafeCert Auditing for facility safety certification' (STEP_03)."""
    vendor_name: str = Field(..., description="Counterparty name")
    amount: float = Field(..., gt=0, description="Transaction amount")
    requires_approval: bool = Field(
        default=False,
        description="True when amount exceeds the SOP approval threshold",
    )

    @field_validator("requires_approval", mode="before")
    @classmethod
    def _check_threshold(cls, v, info):
        amount = (info.data or {}).get("amount", 0)
        return bool(amount and amount > 5000.0)

class Step04Schema(BaseModel):
    """Validates 'Pay Twilio API usage invoice' (STEP_04)."""
    vendor_name: str = Field(..., description="Counterparty name")
    amount: float = Field(..., gt=0, description="Transaction amount")
    requires_approval: bool = Field(
        default=False,
        description="True when amount exceeds the SOP approval threshold",
    )

    @field_validator("requires_approval", mode="before")
    @classmethod
    def _check_threshold(cls, v, info):
        amount = (info.data or {}).get("amount", 0)
        return bool(amount and amount > 5000.0)

class Step05Schema(BaseModel):
    """Validates 'Pay Northbridge Staffing for contracted drivers' (STEP_05)."""
    vendor_name: str = Field(..., description="Counterparty name")
    amount: float = Field(..., gt=0, description="Transaction amount")
    requires_approval: bool = Field(
        default=False,
        description="True when amount exceeds the SOP approval threshold",
    )

    @field_validator("requires_approval", mode="before")
    @classmethod
    def _check_threshold(cls, v, info):
        amount = (info.data or {}).get("amount", 0)
        return bool(amount and amount > 5000.0)

class Step06Schema(BaseModel):
    """Validates 'Pay RouteFuel fleet fuel card balance' (STEP_06)."""
    vendor_name: str = Field(..., description="Counterparty name")
    amount: float = Field(..., gt=0, description="Transaction amount")
    requires_approval: bool = Field(
        default=False,
        description="True when amount exceeds the SOP approval threshold",
    )

    @field_validator("requires_approval", mode="before")
    @classmethod
    def _check_threshold(cls, v, info):
        amount = (info.data or {}).get("amount", 0)
        return bool(amount and amount > 5000.0)

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
    state["log"].append(f"intake: {parsed.vendor_name} ${parsed.amount:,.2f}")
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
        f"validate: ${state['amount']:,.2f} "
        f"{'exceeds' if state['requires_approval'] else 'within'} "
        f"threshold (${threshold:,.0f})"
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
        result = workflow.invoke({
            "vendor_name": vendor,
            "amount": amount,
            "memo": "demo",
            "approved_vendor": False,
            "requires_approval": False,
            "approved": None,
            "outcome": "",
            "log": [],
        })
        print(f"\n{vendor} ${amount:,.2f} -> {result['outcome']}")
        for line in result["log"]:
            print(f"   {line}")
