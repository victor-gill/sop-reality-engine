"""Tests for the round-3 review feedback.

Covers: config import hardening (finite-number/string validation, size
cap), report number reconciliation, evidence + regularity per finding,
DRAFT stamping / --final gate, and the .docx export.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from config import (MAX_CONFIG_BYTES, load_config, save_config,
                    validate_config)
from client_report import (build_report, count_edits, to_docx, EDIT)
from financial_normalizer import normalize_ledger
from gap_engine import analyze, find_unmapped_leakage

import tempfile


def _csv(content: str) -> str:
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w")
    f.write(content)
    f.close()
    return f.name


def _recurring_csv(n=6, amount=100.0, start="2026-01-01"):
    dates = pd.date_range(start, periods=n, freq="MS").strftime("%Y-%m-%d")
    rows = "\n".join(f"{d},Steady Vendor,{amount}" for d in dates)
    return _csv(f"Date,Description,Amount\n{rows}\n")


def _minimal_analysis():
    events = normalize_ledger(_recurring_csv())
    return analyze([], events)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def test_config_rejects_nan_inf_negative_thresholds():
    for bad in (float("nan"), float("inf"), float("-inf"), -1, -0.01):
        with pytest.raises(ValueError):
            validate_config({"approval_threshold": bad})
        with pytest.raises(ValueError):
            validate_config({"category_thresholds": {"x": bad}})


def test_config_rejects_non_number_thresholds():
    for bad in ("5000", None, True, [5000]):
        with pytest.raises(ValueError):
            validate_config({"approval_threshold": bad})


def test_config_rejects_non_string_vendors_and_roles():
    with pytest.raises(ValueError):
        validate_config({"auto_pay_vendors": ["ok", 123]})
    with pytest.raises(ValueError):
        validate_config({"roles": {"Operator": ["not", "a", "string"]}})


def test_config_rejects_overlong_strings():
    with pytest.raises(ValueError):
        validate_config({"business_name": "x" * 201})
    with pytest.raises(ValueError):
        validate_config({"auto_pay_vendors": ["y" * 500]})


def test_config_strips_unknown_keys():
    out = validate_config({"approval_threshold": 100,
                           "evil_key": "dropped"})
    assert "evil_key" not in out
    assert out["approval_threshold"] == 100.0


def test_config_rejects_bad_digest_cadence():
    with pytest.raises(ValueError):
        validate_config({"digest_cadence": "hourly"})


def test_config_load_refuses_oversized_file(tmp_path):
    p = tmp_path / "huge.yaml"
    p.write_text("a: " + "x" * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ValueError):
        load_config(p)


def test_config_save_validates_before_writing(tmp_path):
    p = tmp_path / "cfg.yaml"
    with pytest.raises(ValueError):
        save_config({"approval_threshold": float("nan")}, p)
    assert not p.exists()


def test_config_roundtrip_valid(tmp_path):
    p = tmp_path / "cfg.yaml"
    cfg = {"business_name": "Acme", "approval_threshold": 250,
           "auto_pay_vendors": ["Vendor A"],
           "roles": {"Operator": "Dana"},
           "digest_cadence": "daily"}
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded["business_name"] == "Acme"
    assert loaded["approval_threshold"] == 250.0


# ---------------------------------------------------------------------------
# Number reconciliation
# ---------------------------------------------------------------------------

def test_finding_monthly_times_12_equals_annualized():
    findings = find_unmapped_leakage(
        [], normalize_ledger(_recurring_csv()))
    for f in findings:
        assert round(f["monthly_cost"] * 12, 2) == f["annualized"]


def test_summary_monthly_times_12_equals_annualized():
    s = _minimal_analysis()["summary"]
    assert round(s["monthly_leakage"] * 12, 2) == s["annualized_leakage"]


# ---------------------------------------------------------------------------
# Evidence + regularity
# ---------------------------------------------------------------------------

def test_leakage_finding_carries_evidence_fields():
    f = find_unmapped_leakage([], normalize_ledger(_recurring_csv()))[0]
    assert f["transactions"] == 6
    assert f["first_date"] <= f["last_date"]
    assert f["min_amount"] <= f["max_amount"]
    assert f["regularity"] in ("high", "medium", "low", "n/a")


def test_metronome_cadence_is_high_regularity():
    f = find_unmapped_leakage([], normalize_ledger(_recurring_csv()))[0]
    assert f["regularity"] == "high"


def test_report_finding_block_shows_evidence():
    report = build_report(_minimal_analysis(), [], "Test Co")
    assert "Evidence:" in report
    assert "transactions" in report
    assert "cadence regularity:" in report


# ---------------------------------------------------------------------------
# DRAFT stamp / final gate
# ---------------------------------------------------------------------------

def test_default_report_is_stamped_draft():
    report = build_report(_minimal_analysis(), [], "Test Co")
    assert "DRAFT" in report
    assert count_edits(report) > 0


def test_final_report_has_no_draft_stamp_or_edits():
    analysis = _minimal_analysis()
    draft = build_report(analysis, [], "Test Co", final=True)
    # Simulate the completed auditor pass.
    clean = "\n".join(
        l for l in draft.splitlines() if "✏️ EDIT" not in l)
    assert count_edits(clean) == 0
    assert "DRAFT" not in clean


def test_edit_prompts_stand_on_own_lines():
    report = build_report(_minimal_analysis(), [], "Test Co")
    for line in report.splitlines():
        if "✏️ EDIT" in line:
            assert line.startswith(">"), f"mid-line prompt: {line!r}"


# ---------------------------------------------------------------------------
# docx export
# ---------------------------------------------------------------------------

def test_docx_export_produces_readable_document(tmp_path):
    from docx import Document
    report = build_report(_minimal_analysis(), [], "Test Co")
    out = tmp_path / "report.docx"
    to_docx(report, str(out))
    assert out.exists()
    doc = Document(str(out))
    texts = "\n".join(p.text for p in doc.paragraphs)
    assert "Operational Audit Report" in texts
    assert "Executive summary" in texts
    assert "Finding 1" in texts
