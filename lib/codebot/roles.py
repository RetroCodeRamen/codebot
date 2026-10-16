"""Which model, and whether it thinks, for each role the model plays. Defaults reproduce the single-model setup exactly (every role uses
CODEBOT_MODEL with thinking off), so nothing changes unless a setting is given:
    PLANNER_MODEL / PLANNER_THINK     the unattended planner and its repair rounds
    SPLITTER_MODEL / SPLITTER_THINK   the model-driven splitter
    WORKER_THINK                      the worker loop (its model is CODEBOT_MODEL; ladder rung 5 may switch to ESCALATE_MODEL)
    THINK_BUDGET                      extra output tokens a thinking call gets on top of its normal limit (the reasoning counts against num_predict)
A think value is off/on (or false/true), or low/medium/high for models that take levels."""

ROLES = ("planner", "splitter", "worker")
_LEVELS = ("low", "medium", "high")

def model(proj, role):
    base = proj.get("CODEBOT_MODEL", "codebot-model")
    if role == "worker": return base
    return proj.get(f"{role.upper()}_MODEL") or base

def think(proj, role):
    """False, True, or a level string, as Ollama's `think` field takes it."""
    v = str(proj.get(f"{role.upper()}_THINK", "off") or "off").strip().lower()
    if v in _LEVELS: return v
    return v in ("1", "on", "true", "yes")

def think_budget(proj): return proj.get("THINK_BUDGET", 4096, int)

def describe(proj):
    """{role: {"model":..., "think":...}} plus the escalation model, for reports: a trial must say exactly which models it measured."""
    d = {r: {"model": model(proj, r), "think": think(proj, r)} for r in ROLES}
    d["escalate"] = proj.get("ESCALATE_MODEL") or None
    return d
