"""Durable agent messages: one append-only JSONL log.

A line is a message, or a read event ({"event": "read", "ids": [...]}) that says those messages have been dealt with. A message is never
rewritten: its effective state is "read" when a read event names it (logs written before 2026-11-12 marked the message line itself, which is
still honoured). A line cut off by a crash is skipped. A message made by a model turn carries that turn's invocation id (`inv`), and sending
the same (inv, type, recipient) again returns the first one: a turn carried out twice after a crash does not speak twice."""
import json, os, time, uuid
from . import schema as S
from .journal import append_line

def _new_id(): return uuid.uuid4().hex[:12]

def path(run_dir): return os.path.join(run_dir, "messages.jsonl")

def append(run_dir, msg):
    os.makedirs(run_dir, exist_ok=True)
    msg = dict(msg)
    msg.setdefault("id", _new_id())
    msg.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    msg.setdefault("state", "delivered")
    append_line(path(run_dir), json.dumps(msg, ensure_ascii=False))
    return msg

def _rows(run_dir):
    p = path(run_dir)
    if not os.path.isfile(p): return []
    out = []
    with open(p, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try: out.append(json.loads(ln))
                except ValueError: continue
    return out

def iter_messages(run_dir):
    """The messages, oldest first, each with its effective state."""
    rows = _rows(run_dir); read = {i for r in rows if r.get("event") == "read" for i in r.get("ids") or []}
    for r in rows:
        if r.get("event"): continue
        yield {**r, "state": "read"} if r.get("id") in read else r

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

def send(run_dir, sender, recipient, mtype, payload, *, thread=None, correlation=None, reply_to=None, blocking=False, artifacts=None, inv=None):
    if mtype not in S.MESSAGE: raise ValueError(f"bad message type {mtype}")
    if inv:
        for m in iter_messages(run_dir):
            if m.get("inv") == inv and m.get("type") == mtype and m.get("to") == recipient: return m
    return append(run_dir, {**({"inv": inv} if inv else {}),
        "from": sender, "to": recipient, "type": mtype, "payload": payload,
        "thread": thread or _new_id(), "correlation": correlation or _new_id(),
        "reply_to": reply_to, "blocking": bool(blocking), "artifacts": artifacts or []
    })

def answer(run_dir, sender, question, payload, artifacts=None, inv=None):
    return send(run_dir, sender, question["from"], "ANSWER", payload,
                thread=question.get("thread"), correlation=question.get("correlation"),
                reply_to=question["id"], blocking=False, artifacts=artifacts, inv=inv)

def mark_read(run_dir, message_ids):
    """Record that these messages have been dealt with: one appended read event for the ids not read yet (none: nothing is written)."""
    known = {m["id"]: m.get("state") for m in iter_messages(run_dir)}
    ids = [i for i in dict.fromkeys(message_ids) if i in known and known[i] != "read"]
    if not ids: return []
    append_line(path(run_dir), json.dumps({"event": "read", "ids": ids, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    return ids
