"""Tests for the expense classifier (business vs. personal spending).

Run:  .venv/bin/python -m pytest tests/
Covers the three classification layers, the confidence contract, the
LLM budget behavior, and the new config keys.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from config import load_config, validate_config
from expense_classifier import (
    BUSINESS, PERSONAL, TRANSFER, NEEDS_REVIEW,
    classify_expenses, expense_breakdown,
)
from financial_normalizer import clean_vendor


def _events(*rows) -> pd.DataFrame:
    """Build a minimal event log. Each row: (vendor, category, amount)."""
    return pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-01-15"),
        "vendor_payee": clean_vendor(v),
        "vendor_raw": v,
        "amount": a,
        "category": c,
        "source": "bank",
        "frequency": "ad-hoc",
    } for v, c, a in rows])


def _cfg(**over):
    base = {
        "auxiliary_categories": ["Entertainment", "Fast Food"],
        "transfer_categories": ["Bank Transfer"],
        "business_profile": {"industry": "car rental & fleet operations",
                             "description": "12-vehicle rental fleet"},
        "confirmed_business_vendors": [],
        "confirmed_personal_vendors": [],
        "classification_llm_budget": 0,  # no LLM in unit tests by default
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Layer 1 — deterministic rules
# ---------------------------------------------------------------------------

def test_transfer_category_is_not_spending():
    ev = _events(("CHK 5643 transfer", "Bank Transfer", 5000.0))
    out = classify_expenses(ev, _cfg())
    assert out["expense_class"].iloc[0] == TRANSFER
    assert out["expense_confidence"].iloc[0] == "high"


def test_payroll_is_business():
    ev = _events(("Jane Doe", "Payroll", 1200.0))
    ev["source"] = "payroll"
    out = classify_expenses(ev, _cfg())
    assert out["expense_class"].iloc[0] == BUSINESS
    assert out["expense_confidence"].iloc[0] == "high"


def test_learned_business_vendor_beats_heuristic():
    # Netflix looks personal to heuristics, but the client confirmed it
    # as business (e.g. waiting-room screens) — learning outranks.
    ev = _events(("Netflix", "Media", 15.99))
    cfg = _cfg(confirmed_business_vendors=["Netflix"])
    out = classify_expenses(ev, cfg)
    assert out["expense_class"].iloc[0] == BUSINESS
    assert out["expense_confidence"].iloc[0] == "high"


def test_learned_personal_vendor_beats_heuristic():
    # "Shell" looks like fleet fuel, but this client confirmed the
    # owner fuels a personal car on the business card.
    ev = _events(("Shell", "Fuel", 60.0))
    cfg = _cfg(confirmed_personal_vendors=["shell"])
    out = classify_expenses(ev, cfg)
    assert out["expense_class"].iloc[0] == PERSONAL
    assert out["expense_confidence"].iloc[0] == "high"


# ---------------------------------------------------------------------------
# Layer 2 — heuristics (medium confidence, never certain)
# ---------------------------------------------------------------------------

def test_auxiliary_category_is_personal_medium():
    ev = _events(("Dave & Buster's", "Entertainment", 180.0))
    out = classify_expenses(ev, _cfg())
    assert out["expense_class"].iloc[0] == PERSONAL
    assert out["expense_confidence"].iloc[0] == "medium"


def test_business_keyword_is_business_medium():
    ev = _events(("Shell", "Fuel", 85.0))
    out = classify_expenses(ev, _cfg())
    assert out["expense_class"].iloc[0] == BUSINESS
    assert out["expense_confidence"].iloc[0] == "medium"


def test_unknown_vendor_needs_review():
    ev = _events(("Zqxwobble LLC", "Uncategorized", 42.0))
    out = classify_expenses(ev, _cfg())
    assert out["expense_class"].iloc[0] == NEEDS_REVIEW
    assert out["expense_confidence"].iloc[0] == "low"


# ---------------------------------------------------------------------------
# Layer 3 — LLM gating
# ---------------------------------------------------------------------------

def test_no_llm_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ev = _events(("Zqxwobble LLC", "Uncategorized", 42.0))
    out = classify_expenses(ev, _cfg(classification_llm_budget=10),
                            allow_llm=True)
    # No key -> LLM layer silently skips -> still needs_review.
    assert out["expense_class"].iloc[0] == NEEDS_REVIEW


def test_llm_disabled_by_flag():
    ev = _events(("Zqxwobble LLC", "Uncategorized", 42.0))
    out = classify_expenses(ev, _cfg(classification_llm_budget=10),
                            allow_llm=False)
    assert out["expense_class"].iloc[0] == NEEDS_REVIEW


def test_zero_budget_means_no_llm():
    ev = _events(("Zqxwobble LLC", "Uncategorized", 42.0))
    out = classify_expenses(ev, _cfg(classification_llm_budget=0),
                            allow_llm=True)
    assert out["expense_class"].iloc[0] == NEEDS_REVIEW


def test_llm_failure_leaves_needs_review(monkeypatch):
    # Simulate an API/parse failure mid-pipeline: the batch function
    # returns nothing, rows must stay needs_review, never crash.
    import expense_classifier as ec
    monkeypatch.setattr(ec, "_classify_vendors_llm", lambda v, c: {})
    ev = _events(("Zqxwobble LLC", "Uncategorized", 42.0))
    out = classify_expenses(ev, _cfg(classification_llm_budget=10),
                            allow_llm=True)
    assert out["expense_class"].iloc[0] == NEEDS_REVIEW


def test_llm_verdict_applies_to_vendor(monkeypatch):
    import expense_classifier as ec
    monkeypatch.setattr(
        ec, "_classify_vendors_llm",
        lambda v, c: {"zqxwobble": (BUSINESS, "medium",
                                    "fleet parts supplier")})
    ev = _events(("Zqxwobble LLC", "Uncategorized", 42.0),
                 ("Zqxwobble LLC", "Uncategorized", 38.0))
    out = classify_expenses(ev, _cfg(classification_llm_budget=10),
                            allow_llm=True)
    # One vendor decision covers both transactions.
    assert (out["expense_class"] == BUSINESS).all()
    assert (out["expense_confidence"] == "medium").all()


# ---------------------------------------------------------------------------
# Summary + config
# ---------------------------------------------------------------------------

def test_expense_breakdown_totals():
    ev = _events(("Shell", "Fuel", 100.0),
                 ("Netflix", "Entertainment", 50.0),
                 ("Zqxwobble LLC", "Uncategorized", 25.0))
    out = classify_expenses(ev, _cfg())
    bd = expense_breakdown(out)
    assert bd["business"]["total"] == 100.0
    assert bd["personal"]["total"] == 50.0
    assert bd["needs_review"]["total"] == 25.0
    assert bd["business"]["transactions"] == 1


def test_expense_breakdown_empty_when_unclassified():
    ev = _events(("Shell", "Fuel", 100.0))
    assert expense_breakdown(ev) == {}


def test_config_accepts_new_keys(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "business_profile:\n"
        "  industry: fleet\n"
        "  description: rental cars\n"
        "confirmed_business_vendors: [Shell]\n"
        "confirmed_personal_vendors: [Netflix]\n"
        "classification_llm_budget: 5\n")
    cfg = load_config(p)
    assert cfg["business_profile"]["industry"] == "fleet"
    assert cfg["confirmed_business_vendors"] == ["Shell"]
    assert cfg["classification_llm_budget"] == 5


def test_config_rejects_bad_classifier_keys():
    with pytest.raises(ValueError):
        validate_config({"classification_llm_budget": -1})
    with pytest.raises(ValueError):
        validate_config({"classification_llm_budget": "many"})
    with pytest.raises(ValueError):
        validate_config({"business_profile": "fleet"})
    with pytest.raises(ValueError):
        validate_config({"confirmed_business_vendors": "Shell"})
