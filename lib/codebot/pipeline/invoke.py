"""One agent invocation: build context, call model (or mock), return structured action."""
import json
from . import context, mailbox
from .validate import ValidationError

# Constrained output for every pipeline agent turn.
ACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "enum": ["ask", "answer", "findings", "write", "finish", "fail", "noop", "escalate"]},
        "to": {"type": "string"},
        "question_id": {"type": "string"},
        "text": {"type": "string"},
        "path": {"type": "string"},
        "content": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "where": {"type": "string"}, "what": {"type": "string"}, "serious": {"type": "string"}
        }}},
        "artifact_name": {"type": "string"},
        "reason": {"type": "string"}
    }
}

class InvokeError(Exception): pass

def call_model(proj, messages, schema, model=None, temperature=0.2, num_predict=2000, chat_fn=None):
    """chat_fn(proj, messages, schema=..., model=..., ...) -> {content: str} for tests."""
    if chat_fn is not None:
        return chat_fn(proj, messages, schema=schema, model=model, temperature=temperature, num_predict=num_predict)
    from codebot import ollama
    return ollama.chat(proj, messages, schema=schema, model=model, temperature=temperature, num_predict=num_predict)

def parse_action(content):
    try: obj = json.loads(content)
    except (TypeError, ValueError) as e: raise InvokeError(f"not JSON: {e}")
    if not isinstance(obj, dict) or "action" not in obj: raise InvokeError("missing action")
    if obj["action"] not in ACTION_SCHEMA["properties"]["action"]["enum"]: raise InvokeError(f"bad action {obj['action']}")
    return obj

def model_name(agent, bundle):
    m = agent.get("model")
    if isinstance(m, dict): return m.get("name") or None
    if isinstance(m, str): return m
    return (bundle.get("project") or {}).get("model")

def invoke(bundle, run_dir, member_id, task_text, *, proj=None, chat_fn=None, extra_files=None):
    """Run one model call for member_id. Returns (action_dict, agent, raw_content)."""
    messages, agent = context.build_messages(bundle, run_dir, member_id, task_text, extra_files=extra_files, proj=proj)
    model = model_name(agent, bundle)
    temp = 0.2
    if isinstance(agent.get("model"), dict) and agent["model"].get("temperature") is not None:
        temp = float(agent["model"]["temperature"])
    out = call_model(proj, messages, ACTION_SCHEMA, model=model, temperature=temp, chat_fn=chat_fn)
    action = parse_action(out.get("content") or "")
    return action, agent, out.get("content") or ""
