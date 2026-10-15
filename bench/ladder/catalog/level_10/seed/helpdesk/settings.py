"""Optional settings file (JSON) next to the data file: {"sla_hours": {"urgent": 2, ...}}. Missing file = defaults."""
import json, os
from . import config
from .errors import ValidationError

def load(path):
    """Settings dict from `path` (an empty dict when the file does not exist)."""
    if not os.path.exists(path): return {}
    try:
        with open(path) as f: data = json.load(f)
    except (OSError, ValueError) as e: raise ValidationError(f"cannot read settings {path}: {e}")
    if not isinstance(data, dict): raise ValidationError("settings must be a JSON object")
    return data

def apply(settings):
    """Apply overrides to the config module. Returns the list of keys that were changed."""
    changed = []
    sla = settings.get("sla_hours")
    if sla is not None:
        if not isinstance(sla, dict) or any(k not in config.PRIORITIES or isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0 for k, v in sla.items()): raise ValidationError("sla_hours must map priorities to positive numbers")
        config.SLA_HOURS.update(sla); changed.append("sla_hours")
    return changed
