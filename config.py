"""Business configuration loader — the customizable layer.

One YAML file (`business_config.yaml`) holds the business's rules:
approval thresholds, auto-pay vendors, team roles. The audit engine,
task router, and generated agent workflows all read from here, so the
owner customizes behavior by editing values, not code.
"""

from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).parent / "business_config.yaml"

_defaults = {
    "business_name": "My Business",
    "approval_threshold": 5000,
    "category_thresholds": {},
    "auto_pay_vendors": [],
    "always_review_vendors": [],
    "roles": {},
    "digest_cadence": "weekly",
    "digest_min_leakage": 500,
}


def load_config(path: str | Path | None = None) -> dict:
    """Load the business config, filling in defaults for missing keys."""
    cfg = dict(_defaults)
    p = Path(path) if path else DEFAULT_PATH
    if p.exists():
        with open(p, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        cfg.update(loaded)
    return cfg


def save_config(cfg: dict, path: str | Path | None = None) -> str:
    """Write the business config back to YAML."""
    p = Path(path) if path else DEFAULT_PATH
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)
    return str(p)


def threshold_for(cfg: dict, category: str | None = None) -> float:
    """Effective approval threshold, with per-category overrides."""
    if category:
        override = (cfg.get("category_thresholds") or {}).get(category)
        if override is not None:
            return float(override)
    return float(cfg.get("approval_threshold", 5000))


def display_name(cfg: dict, role: str) -> str:
    """'Payroll Lead' -> 'Payroll Lead (Maria)' when a name is assigned."""
    name = (cfg.get("roles") or {}).get(role, "")
    return f"{role} ({name})" if name else role
