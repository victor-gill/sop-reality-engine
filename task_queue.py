"""
Task Queue — Phase 4: findings become assigned work.

Each gap-analysis finding is converted into a task routed to the role
responsible for it. This is the meeting killer: instead of a status
meeting to surface problems, the system pushes each problem to the
person who owns it, with a priority and a clear first action.

This is the first brick of the business-OS vision: the engine doesn't
just audit — it runs the operation by routing work to people.
"""

import json
from datetime import date

STATUSES = ("open", "in_progress", "resolved")


def _assignee(finding: dict, steps_by_id: dict) -> str:
    """Route each finding type to its owning role."""
    ftype = finding["type"]
    if ftype in ("unmapped_leakage", "threshold_review"):
        # Money moving with no paper trail -> the operator owns it.
        return "Operator"
    step = steps_by_id.get(finding.get("step_id", ""), {})
    return step.get("assigned_role") or "Operator"


def _priority(finding: dict) -> str:
    if finding["type"] == "threshold_review":
        return "P1"  # approval breach — urgent by definition
    if finding["type"] == "unmapped_leakage":
        return "P1" if finding.get("annualized", 0) > 1000 else "P2"
    if finding["type"] == "sop_deviation":
        return "P2"
    return "P3"


def _title(finding: dict) -> str:
    t = finding["type"]
    if t == "unmapped_leakage":
        return (f"Investigate unmapped {finding['cadence']} spend: "
                f"{finding['vendor_display']} (~${finding['monthly_cost']:,.2f}/mo)")
    if t == "sop_deviation":
        return f"Missing execution: {finding['step_id']} — no activity found"
    if t == "threshold_review":
        return (f"Verify approval on file: ${finding['amount']:,.2f} to "
                f"{finding['vendor']} ({finding['date']})")
    return (f"Fix cadence drift: {finding['step_id']} — expected every "
            f"{finding['expected_every_days']}d, running every "
            f"{finding['actual_every_days']:.0f}d")


def _first_action(finding: dict) -> str:
    t = finding["type"]
    if t == "unmapped_leakage":
        return ("Confirm what this subscription is for. Keep it: add an SOP step. "
                "Kill it: cancel and confirm the charge stops.")
    if t == "sop_deviation":
        return ("Confirm whether this process still exists. If yes, find why "
                "no spend is flowing. If no, retire the SOP step.")
    if t == "threshold_review":
        return "Locate the signed approval for this payment and attach it here."
    return ("Decide the correct cadence, update the SOP step, and set a "
            "calendar reminder for the next run.")


def generate_tasks(analysis: dict, steps: list[dict]) -> list[dict]:
    """Convert every finding into a routed, prioritized task."""
    steps_by_id = {s["step_id"]: s for s in steps}
    findings = (analysis["threshold_reviews"] + analysis["unmapped_leakage"]
                + analysis["sop_deviations"] + analysis["cycle_gaps"])
    tasks = []
    for i, f in enumerate(findings, 1):
        tasks.append({
            "task_id": f"T-{i:03d}",
            "title": _title(f),
            "detail": f["detail"],
            "first_action": _first_action(f),
            "type": f["type"],
            "assignee": _assignee(f, steps_by_id),
            "priority": _priority(f),
            "amount": f.get("annualized") or f.get("amount"),
            "created": date.today().isoformat(),
            "status": "open",
        })
    # P1 first, then by money at stake.
    order = {"P1": 0, "P2": 1, "P3": 2}
    return sorted(tasks,
                  key=lambda t: (order[t["priority"]],
                                 -(t["amount"] or 0)))


def save_tasks(tasks: list[dict], path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2)


def load_tasks(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def set_status(tasks: list[dict], task_id: str, status: str) -> list[dict]:
    assert status in STATUSES, f"status must be one of {STATUSES}"
    for t in tasks:
        if t["task_id"] == task_id:
            t["status"] = status
    return tasks
