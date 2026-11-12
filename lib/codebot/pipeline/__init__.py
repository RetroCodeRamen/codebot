"""Pipeline graph: modular multi-agent orchestration for codebot (stdlib only).

See `docs/architecture/modular-agent-orchestration.md`.
"""
from .load import load_project, load_doc, resolve_path, load_and_validate
from .validate import validate_project, validate_doc, ValidationError, SCHEMA
from . import schedule, runtime, mailbox, artifacts, design, scope

__all__ = [
    "SCHEMA", "ValidationError", "load_doc", "load_project", "load_and_validate", "resolve_path",
    "validate_project", "validate_doc", "schedule", "runtime", "mailbox", "artifacts", "design", "scope"
]
