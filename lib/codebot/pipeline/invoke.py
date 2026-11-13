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
        "action": {"type": "string", "enum": ["list", "read", "write", "check", "run", "ask", "answer", "findings", "finish", "fail", "noop", "escalate"]},
        "command": {"type": "string"},
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

NUM_PREDICT = 4000          # a whole file travels inside one JSON string (an agent's model.num_predict overrides it)

def schema_for(acts):
    """The action schema with only the actions this agent may take now: the model cannot pick one it has no right to."""
    if not acts: return ACTION_SCHEMA
    props = dict(ACTION_SCHEMA["properties"]); props["action"] = {"type": "string", "enum": list(acts)}
    return {**ACTION_SCHEMA, "properties": props}

def call_model(proj, messages, schema, model=None, temperature=0.2, num_predict=NUM_PREDICT, chat_fn=None, timeout=None):
    """chat_fn(proj, messages, schema=..., model=..., ...) -> {content: str} for tests. timeout (seconds) is what is left of a time budget: the
    call is then made once, not retried, so a hung model cannot outlast it by much."""
    if chat_fn is not None:
        return chat_fn(proj, messages, schema=schema, model=model, temperature=temperature, num_predict=num_predict)
    from codebot import ollama
    from codebot import drafts
    watch = drafts.loop_watch(json_text=True)          # a file written over and over inside the JSON is stopped, not run to the cap
    if timeout: return ollama.chat(proj, messages, schema=schema, model=model, temperature=temperature, num_predict=num_predict, timeout=timeout, retries=1, stop_if=watch)
    return ollama.chat(proj, messages, schema=schema, model=model, temperature=temperature, num_predict=num_predict, stop_if=watch)

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

def invoke(bundle, run_dir, member_id, task_text, *, proj=None, chat_fn=None, extra_files=None, acts=None, history="", timeout=None):
    """Run one model call for member_id. Returns (action_dict, agent, raw_content, usage). acts = the actions it may take; history = its own
    earlier turns; usage = {prompt_tokens, gen_tokens, secs} as the server reported them (zeros from a test's chat_fn)."""
    messages, agent = context.build_messages(bundle, run_dir, member_id, task_text, extra_files=extra_files, proj=proj, acts=acts, history=history)
    model = model_name(agent, bundle)
    temp = 0.2
    if isinstance(agent.get("model"), dict) and agent["model"].get("temperature") is not None:
        temp = float(agent["model"]["temperature"])
    cap = NUM_PREDICT
    if isinstance(agent.get("model"), dict) and agent["model"].get("num_predict"): cap = int(agent["model"]["num_predict"])
    out = call_model(proj, messages, schema_for(acts), model=model, temperature=temp, num_predict=cap, chat_fn=chat_fn, timeout=timeout)
    usage = {k: out.get(k) or 0 for k in ("prompt_tokens", "gen_tokens", "secs")}
    if out.get("done_reason") == "loop":
        raise InvokeError("your reply was stopped because it kept writing the same lines again (the same code over and over with new names); "
                          "write the file once, each function once, only what SPEC.md asks for")
    if out.get("done_reason") == "length":
        raise InvokeError(f"your reply was cut off after {cap} tokens before the JSON object ended; reply with one short action: a shorter command, fewer findings, or a smaller file")
    action = parse_action(out.get("content") or "")
    return action, agent, out.get("content") or "", usage
