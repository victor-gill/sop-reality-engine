"""Business configuration loader — the customizable layer.

One YAML file (`business_config.yaml`) holds the business's rules:
approval thresholds, auto-pay vendors, team roles. The audit engine,
task router, and generated agent workflows all read from here, so the
owner customizes behavior by editing values, not code.
"""

from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).parent / "business_config.yaml"

# Upload/import guard: refuse absurdly large configs before parsing.
MAX_CONFIG_BYTES = 100_000
# String fields are names and labels, not documents.
MAX_STR_LEN = 200

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


def _finite_nonneg(value, name: str) -> float:
    """Money values must be real, finite, non-negative numbers.

    Rejects NaN, inf, negatives, bools, and strings — a crafted config
    must not smuggle non-numeric values into threshold comparisons.
    """
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got "
                         f"{type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite (no NaN/inf)")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return float(value)


def _plain_str(value, name: str, limit: int = MAX_STR_LEN) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got "
                         f"{type(value).__name__}")
    if len(value) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return value


def validate_config(cfg: dict) -> dict:
    """Type-check every config value. Raises ValueError on anything
    malformed or hostile. Unknown keys are stripped (documented)."""
    if not isinstance(cfg, dict):
        raise ValueError("config must be a mapping")
    out = {}
    out["business_name"] = _plain_str(cfg.get("business_name", "My Business"),
                                      "business_name")
    out["approval_threshold"] = _finite_nonneg(
        cfg.get("approval_threshold", 5000), "approval_threshold")
    ct = cfg.get("category_thresholds") or {}
    if not isinstance(ct, dict):
        raise ValueError("category_thresholds must be a mapping")
    out["category_thresholds"] = {
        _plain_str(k, "category name"): _finite_nonneg(v, f"threshold for {k}")
        for k, v in ct.items()
    }
    for key in ("auto_pay_vendors", "always_review_vendors"):
        vals = cfg.get(key) or []
        if not isinstance(vals, list):
            raise ValueError(f"{key} must be a list")
        out[key] = [_plain_str(v, key) for v in vals]
    roles = cfg.get("roles") or {}
    if not isinstance(roles, dict):
        raise ValueError("roles must be a mapping")
    out["roles"] = {_plain_str(k, "role"): _plain_str(v, f"person for {k}")
                     for k, v in roles.items()}
    cadence = cfg.get("digest_cadence", "weekly")
    if cadence not in ("weekly", "daily"):
        raise ValueError("digest_cadence must be 'weekly' or 'daily'")
    out["digest_cadence"] = cadence
    out["digest_min_leakage"] = _finite_nonneg(
        cfg.get("digest_min_leakage", 500), "digest_min_leakage")
    return out


def load_config(path: str | Path | None = None) -> dict:
    """Load the business config, filling in defaults for missing keys."""
    cfg = dict(_defaults)
    p = Path(path) if path else DEFAULT_PATH
    if p.exists():
        if p.stat().st_size > MAX_CONFIG_BYTES:
            raise ValueError(f"config file exceeds "
                             f"{MAX_CONFIG_BYTES} bytes — refusing to parse")
        with open(p, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        cfg.update(loaded)
    return validate_config(cfg)


def save_config(cfg: dict, path: str | Path | None = None) -> str:
    """Validate, then write the business config back to YAML."""
    clean = validate_config(cfg)
    p = Path(path) if path else DEFAULT_PATH
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(clean, f, sort_keys=False, default_flow_style=False)
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
