"""Approval store — fixed module, no generated code.

The agent workflow writes approval requests as JSON; the owner decides
in the app. This module reads and resolves those requests. It is
SOP-independent on purpose: the app must never need to execute
generated workflow code just to approve or reject a payment.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DECISIONS = ("approved", "rejected")


def pending_requests(approvals_dir: str | Path) -> list[dict]:
    """All requests awaiting a decision, newest first."""
    pending = Path(approvals_dir) / "pending"
    if not pending.exists():
        return []
    reqs = []
    for p in sorted(pending.glob("*.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            reqs.append({"txn_id": p.stem, **json.loads(p.read_text())})
        except (json.JSONDecodeError, OSError):
            continue  # skip corrupt files, never crash the tab
    return reqs


def decided_requests(approvals_dir: str | Path, limit: int = 20) -> list[dict]:
    """Recently decided requests, newest first."""
    decided = Path(approvals_dir) / "decided"
    if not decided.exists():
        return []
    reqs = []
    for p in sorted(decided.glob("*.json"),
                    key=lambda x: x.stat().st_mtime,
                    reverse=True)[:limit]:
        try:
            reqs.append({"txn_id": p.stem, **json.loads(p.read_text())})
        except (json.JSONDecodeError, OSError):
            continue
    return reqs


def resolve_approval(approvals_dir: str | Path, txn_id: str, decision: str,
                     decided_by: str = "owner") -> dict:
    """Complete a pending approval.

    decision is 'approved' or 'rejected'. Moves the request from
    pending/ to decided/ and returns the decision record. Raises
    FileNotFoundError when there is no such pending request and
    ValueError on a bad decision — both are shown in the UI, never
    swallowed.
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    base = Path(approvals_dir)
    pending = base / "pending" / f"{txn_id}.json"
    if not pending.exists():
        raise FileNotFoundError(f"no pending approval for {txn_id}")
    request = json.loads(pending.read_text())
    decided_dir = base / "decided"
    decided_dir.mkdir(parents=True, exist_ok=True)
    record = {
        **request,
        "status": decision,
        "decided_by": decided_by,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "outcome": "APPROVED_PAID" if decision == "approved" else "REJECTED",
    }
    (decided_dir / f"{txn_id}.json").write_text(json.dumps(record, indent=2))
    pending.unlink()
    return record
