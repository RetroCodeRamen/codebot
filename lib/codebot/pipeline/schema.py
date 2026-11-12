"""codebot.pipeline/v1: field names and allowed enums. Validation lives in validate.py."""

SCHEMA = "codebot.pipeline/v1"
KINDS = ("agent", "team", "workflow", "project")

# Memory: how much prior conversation an agent may carry into the next invocation.
MEMORY = ("none", "invocation", "task", "project", "persistent")

# Edge / connection intent (team links and workflow edges share these words).
LINK = ("agent", "repository", "both")

# Workflow node kinds. Fan-out queues work; it never starts concurrent inference.
NODE = ("start", "end", "run", "gate", "fan_out", "join", "loop", "approve", "handoff")

# Message types agents may declare (scheduler uses these in M3+).
MESSAGE = ("TASK", "QUESTION", "ANSWER", "FINDING", "DECISION", "RESULT", "ARTIFACT", "ERROR", "ESCALATION")

# Agent runtime states (scheduler M2+); listed here so configs and docs share one vocabulary.
STATE = ("READY", "RUNNING", "WAITING", "SUSPENDED", "COMPLETED", "FAILED", "CANCELLED")

ID = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
PATH_GLOB = r"^[A-Za-z0-9_./\*\?\-\[\]]{1,200}$"   # relative workspace paths / globs only; no absolute, no ..
