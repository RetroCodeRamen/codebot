"""Durable agent messages. One JSONL log + index of open threads for the scheduler."""
import json, os, time, uuid
from . import schema as S

def _new_id(): return uuid.uuid4().hex[:12]

def path(run_dir): return os.path.join(run_dir, "messages.jsonl")

def append(run_dir, msg):
    os.makedirs(run_dir, exist_ok=True)
    msg = dict(msg)
    msg.setdefault("id", _new_id())
    msg.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    msg.setdefault("state", "delivered")
    with open(path(run_dir), "a", encoding="utf-8") as f: f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg

def iter_messages(run_dir):
    p = path(run_dir)
    if not os.path.isfile(p): return
    with open(p, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try: yield json.loads(ln)
                except ValueError: continue

def inbox(run_dir, agent_id, types=None, unread_only=False):
    out = []
    for m in iter_messages(run_dir):
        if m.get("to") != agent_id: continue
        if types and m.get("type") not in types: continue
        if unread_only and m.get("state") == "read": continue
        out.append(m)
    return out

def pending_questions(run_dir):
    """QUESTIONs that still lack a matching ANSWER (by correlation/reply_to)."""
    answered = {m.get("reply_to") or m.get("correlation") for m in iter_messages(run_dir) if m.get("type") == "ANSWER"}
    return [m for m in iter_messages(run_dir) if m.get("type") == "QUESTION" and m.get("id") not in answered and m.get("blocking", True)]

def send(run_dir, sender, recipient, mtype, payload, *, thread=None, correlation=None, reply_to=None, blocking=False, artifacts=None):
    if mtype not in S.MESSAGE: raise ValueError(f"bad message type {mtype}")
    return append(run_dir, {
        "from": sender, "to": recipient, "type": mtype, "payload": payload,
        "thread": thread or _new_id(), "correlation": correlation or _new_id(),
        "reply_to": reply_to, "blocking": bool(blocking), "artifacts": artifacts or []
    })

def answer(run_dir, sender, question, payload, artifacts=None):
    return send(run_dir, sender, question["from"], "ANSWER", payload,
                thread=question.get("thread"), correlation=question.get("correlation"),
                reply_to=question["id"], blocking=False, artifacts=artifacts)

def mark_read(run_dir, message_ids):
    """Rewrite log marking ids as read (small logs only; pipeline runs stay modest)."""
    ids = set(message_ids)
    if not ids or not os.path.isfile(path(run_dir)): return
    rows = list(iter_messages(run_dir))
    tmp = path(run_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for m in rows:
            if m.get("id") in ids: m = {**m, "state": "read"}
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    os.replace(tmp, path(run_dir))
