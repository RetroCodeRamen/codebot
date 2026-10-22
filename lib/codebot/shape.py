"""From a description to a build: a short design conversation, then the drafts run, then a closing review.

The window is the only place this starts. `codebot new` still takes a finished spec. A design lives in projects/<name>/shape.json
with WORKFLOW="shape" until the person presses Build, which writes SPEC.md, switches the project to drafts and starts the usual loop.
The loop calls `python3 -m codebot.shape review` when that build stops, so the closing review and the "it is ready" notice still happen
if the window is closed. The review reports problems. It does not change the code: a change would need the examples run again, and a
finished drafts project is not restarted.
"""
import json, os, re, subprocess, sys, threading, time
from . import drafts, jobs, ollama, projstate as P
from .config import Project, read_env
from .gui import settings as S

chat = ollama.chat
_threads, _locks = {}, {}

class ShapeError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg); self.code = code

def _lock(home, name):
    return _locks.setdefault((home, name), threading.Lock())

def _dir(home, name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", name or ""): raise ShapeError("that name will not work")
    return os.path.join(home, "projects", name)

def _path(home, name): return os.path.join(_dir(home, name), "shape.json")

def _load(home, name):
    st = P.read_json(_path(home, name))
    if not isinstance(st, dict): raise ShapeError("no design for this project", 404)
    return st

def _save(home, name, st):
    d = _dir(home, name); os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "shape.json.tmp")
    with open(tmp, "w") as f: json.dump(st, f, indent=1, ensure_ascii=False)
    os.replace(tmp, os.path.join(d, "shape.json"))

def _env_update(path, updates):
    """Replace or append KEY="value" lines. Values are the same shape bin/codebot sources."""
    try:
        with open(path) as f: lines = f.read().splitlines()
    except OSError: lines = []
    seen = set()
    out = []
    for ln in lines:
        m = S.RE_LINE.match(ln)
        if m and m.group(2) in updates:
            k = m.group(2); _put(k, updates[k]); out.append(f'{k}="{updates[k]}"'); seen.add(k)
        else: out.append(ln)
    for k, v in updates.items():
        if k not in seen: _put(k, v); out.append(f'{k}="{v}"')
    tmp = path + ".tmp"
    with open(tmp, "w") as f: f.write("\n".join(out) + "\n")
    os.replace(tmp, path)

def _put(k, v):
    if S.UNSAFE.search(str(v) or ""): raise ShapeError(f"{k} has a character that cannot go in a settings file")

def _level(v, default=1):
    try: n = int(v)
    except (TypeError, ValueError): return default
    return n if 0 <= n <= 3 else default

def _home(home): return os.path.realpath(home)

def _owner_sentence(owner):
    if not owner: return "The model is already in use."
    kind, name = owner.get("kind"), owner.get("name") or ""
    if kind == "project": return f"A build is already running{(' for ' + name) if name else ''}."
    if kind == "benchmark": return "A benchmark is using the model right now."
    if kind == "planning": return f"A design is already being read{(' (' + name + ')') if name else ''}."
    return f"The model is already in use ({jobs.describe(owner)})."

def _ensure_free(home):
    owner = jobs.current(home)
    if owner: raise ShapeError(_owner_sentence(owner), 409)

def slug(text):
    stop = {"a", "an", "the", "to", "for", "of", "and", "or", "my", "me", "i", "want", "need", "app", "application", "program", "please", "that", "this", "with"}
    words = [w for w in re.findall(r"[A-Za-z0-9]+", (text or "").lower()) if w not in stop]
    name = "-".join(words[:3])[:40].strip("-")
    return name or "project"

def _unique(home, name):
    if not os.path.exists(os.path.join(home, "projects", name)): return name
    for i in range(2, 50):
        n = f"{name[:36]}-{i}"
        if not os.path.exists(os.path.join(home, "projects", n)): return n
    raise ShapeError("too many projects with that name")

def _busy_live(home, name, st):
    b = st.get("busy")
    if not b or not b.get("pid"): return False
    if b["pid"] == os.getpid():
        t = _threads.get((home, name))
        return bool(t and t.is_alive())
    return os.path.exists(f"/proc/{b['pid']}")

def _clear_dead(home, name, st):
    if st.get("busy") and not _busy_live(home, name, st):
        st["busy"] = None
        st["error"] = st.get("error") or "That step stopped before it finished (the window's server was closed). Try again."
        st["retry"] = st.get("retry") or "plan"
        _save(home, name, st)
    return st

def _spawn(home, name, what, fn):
    home = _home(home)
    with _lock(home, name):
        st = _load(home, name)
        if _busy_live(home, name, st): raise ShapeError("Still working on the last step.", 409)
        st["busy"] = {"pid": os.getpid(), "what": what, "since": time.time()}
        st["error"] = None
        st["retry"] = {"Reading what you wrote": "opening", "Writing the plan": "plan", "Reading the code": "review"}.get(what, "plan")
        _save(home, name, st)
    def run():
        try: fn()
        except Exception as e:
            st2 = _load(home, name); st2["busy"] = None; st2["error"] = _friendly(e); _save(home, name, st2)
        finally: _threads.pop((home, name), None)
    t = threading.Thread(target=run, daemon=True)
    _threads[(home, name)] = t
    t.start()

def _friendly(e):
    if isinstance(e, jobs.Busy): return _owner_sentence(e.owner)
    if isinstance(e, ShapeError): return str(e)
    if isinstance(e, ollama.ModelError):
        if "cannot reach" in str(e): return "The model server is not running, so this cannot be read yet. Start it with codebot setup, then try again."
        return f"The model could not answer ({e})."
    return f"Something went wrong ({type(e).__name__}: {e})."[:400]

# ---------- what the model is asked ----------
_Q = {"type": "object", "additionalProperties": False, "required": ["understood", "questions"], "properties": {
    "understood": {"type": "string"}, "suggest_security": {"type": "integer"},
    "questions": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["ask", "why"],
        "properties": {"ask": {"type": "string"}, "why": {"type": "string"}}}}}}
_PLAN = {"type": "object", "additionalProperties": False, "required": ["ready", "questions", "title", "summary", "pieces", "decisions", "spec"], "properties": {
    "ready": {"type": "boolean"}, "title": {"type": "string"}, "summary": {"type": "string"}, "spec": {"type": "string"},
    "questions": _Q["properties"]["questions"], "decisions": {"type": "array", "items": {"type": "string"}},
    "pieces": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "does"],
        "properties": {"name": {"type": "string"}, "does": {"type": "string"}}}}}}
_FINDING = {"type": "object", "additionalProperties": False, "required": ["where", "what", "serious"], "properties": {
    "where": {"type": "string"}, "what": {"type": "string"}, "serious": {"type": "string"}}}
_REVIEW = {"type": "object", "additionalProperties": False, "required": ["summary", "findings"], "properties": {
    "summary": {"type": "string"}, "findings": {"type": "array", "items": _FINDING}}}

ASK = """You are helping one person decide what a small program should be, before any code is written.
Ask only questions whose answers would change what gets built. Three to five, one sentence each.
Skip anything they already said. Do not ask about languages, frameworks, or file layouts.
If who it is for and what it must do are already clear, return an empty question list.
suggest_security is 0 for a throwaway, 1 if only this computer runs it, 2 if other computers on their network can reach it, 3 if anyone on the internet can.
Write the way you would ask a person across a table."""

PLAN = """Turn the conversation into two things: a design a person can read, and a specification a checker can run.
The design (title, summary, pieces, decisions) is plain language. The specification is the spec field, and it is Markdown:
a title, one paragraph on what the program is, a build command in backticks when it is not a single Python file (for example `go build -o calc .`),
then a heading Requirements and numbered requirements. Each requirement that can be checked has a worked example in backticks, only in these forms:
`double(2)` is `4`    `parse("")` raises `ValueError`    `calc 2 + 3` prints `5`
`calc 1 / 0` prints `error: division by zero` to standard error, nothing to standard output, and exits with status 1
`calc` with standard input `2 + 3\\n` prints `5` and exits with status 0
A Python function example also says where it lives: `mathx.py` provides `double(n)`.
Do not invent behavior they ruled out. Set ready to false and ask at most four questions only when an answer would still change what gets built.
Otherwise ready is true and questions is empty."""

CODE_BRIEF = ("Look for basic defects only: two parts using the same data without regard to order, code that is never used, "
              "errors that are ignored, and resources that are not closed. Ignore naming and style. "
              "Do not write an attack, a payload, or steps to exploit anything. Say where, what is wrong, and how serious "
              "(low, real, or serious). If a category has nothing, do not invent one. summary is two sentences a person can read.")

def security_brief(level):
    """What the closing security pass looks for. Level 0 is not called."""
    common = (" Do not write an attack, a payload, or steps to exploit anything. Say where, what is wrong, and how serious "
              "(low, real, or serious). If nothing is wrong, say so in the summary and return no findings.")
    if level <= 1: return ("This program runs only on the owner's computer. Look for commands built from outside input, "
                           "reading or writing outside the program's own folder, and opening a network port." + common)
    if level == 2: return ("Other computers on the owner's network can reach this program. Look for commands built from outside input, "
                           "reading or writing outside its own folder, listening on every interface, no check of who is calling, "
                           "and any way it could gain more access than the person who started it." + common)
    return ("Anyone on the internet can reach this program. Look for commands or queries built from what a visitor sends, "
            "one person's data affecting another's, secrets stored in the tree, listening without a check of who is calling, "
            "and any way a visitor could make the program run a command or gain more access." + common)

def _obj(content):
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try: o = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m: raise ShapeError("The reply was not readable. Try again.")
        try: o = json.loads(m.group(0))
        except ValueError: raise ShapeError("The reply was not readable. Try again.")
    if not isinstance(o, dict): raise ShapeError("The reply was not readable. Try again.")
    return o

def _questions(reply):
    out = []
    for q in (reply.get("questions") or [])[:5]:
        if isinstance(q, str): q = {"ask": q, "why": ""}
        if not isinstance(q, dict): continue
        ask = str(q.get("ask") or "").strip()
        if ask: out.append({"ask": ask[:500], "why": str(q.get("why") or "").strip()[:300]})
    for i, q in enumerate(out, 1): q["id"] = f"q{i}"
    return out

def _pieces(reply):
    out = []
    for p in (reply.get("pieces") or [])[:12]:
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            out.append({"name": str(p["name"]).strip()[:80], "does": str(p.get("does") or "").strip()[:240]})
    return out

def _call(proj, system, user, schema, n):
    with jobs.Hold(proj.home, "planning" if schema is not _REVIEW else "project", proj.name, wait=1.0):
        r = chat(proj, [{"role": "system", "content": system}, {"role": "user", "content": user}], schema=schema, temperature=0.3, num_predict=n, timeout=900, retries=2, role="shape")
    return _obj(r.get("content") or "")

def _transcript(st):
    lines = ["The person wants:\n" + (st.get("idea") or "")]
    for group in list(st.get("history") or []) + ([st["questions"]] if any(q.get("answer") for q in st.get("questions") or []) else []):
        lines.append("\nQuestions and answers:")
        for q in group:
            if q.get("answer"): lines.append(f"Q: {q['ask']}\nA: {q['answer']}")
    for note in st.get("notes") or []: lines.append("\nThey added:\n" + note)
    return "\n".join(lines)[:20000]

def _opening(home, name):
    proj = Project(name, home)
    reply = _call(proj, ASK, _transcript(_load(home, name)), _Q, 700)
    st = _load(home, name)
    st["understood"] = str(reply.get("understood") or "").strip()[:600]
    sug = reply.get("suggest_security")
    st["suggest_security"] = sug if isinstance(sug, int) and 0 <= sug <= 3 else None
    qs = _questions(reply)
    if qs:
        st["question_rounds"] = 1; st["phase"] = "questions"; st["questions"] = qs; st["busy"] = None; _save(home, name, st); return
    st["question_rounds"] = 0; _save(home, name, st)
    _plan(home, name)

def _archive(st):
    if any(q.get("answer") for q in st.get("questions") or []):
        st.setdefault("history", []).append(st["questions"])

def _plan(home, name):
    proj = Project(name, home)
    st = _load(home, name); _archive(st); st["questions"] = []; _save(home, name, st)
    reply = _call(proj, PLAN, _transcript(st), _PLAN, 2500)
    spec = str(reply.get("spec") or "").strip()
    if spec and drafts.coverage(spec)["examples"] == 0:
        again = _call(proj, PLAN, _transcript(st) + "\n\nThe specification below has no example a checker can run. Rewrite spec so each numbered requirement has one example in the forms you were given.\n\n" + spec[:12000], _PLAN, 2500)
        if drafts.coverage(str(again.get("spec") or ""))["examples"]:
            reply = again; spec = str(again.get("spec") or "").strip()
    st = _load(home, name)
    qs = _questions(reply)
    if not reply.get("ready") and qs and st.get("question_rounds", 0) < 2:
        st["question_rounds"] = st.get("question_rounds", 0) + 1
        st["phase"] = "questions"; st["questions"] = qs; st["busy"] = None
        if reply.get("summary"): st["understood"] = str(reply["summary"]).strip()[:600]
        _save(home, name, st); return
    if not spec.strip(): raise ShapeError("The plan came back without anything to build. Try again.")
    if not spec.endswith("\n"): spec += "\n"
    st["spec"] = spec
    st["coverage"] = drafts.coverage(spec) if spec else None
    open_points = [q["ask"] for q in qs]
    st["outline"] = {"title": str(reply.get("title") or "").strip()[:120], "summary": str(reply.get("summary") or "").strip()[:1200],
                     "pieces": _pieces(reply), "decisions": [str(x).strip()[:240] for x in (reply.get("decisions") or []) if str(x).strip()][:12],
                     "open": open_points}
    st["phase"] = "ready"; st["questions"] = []; st["busy"] = None; st["error"] = None
    _save(home, name, st)

# ---------- the window drives these ----------
def begin(home, idea, name=None, security=1):
    home = _home(home); idea = str(idea or "").strip()
    if len(idea) < 12: raise ShapeError("Say a little more about what you want.")
    if len(idea) > 20000: raise ShapeError("That is too long to start from. Keep it to the idea, and the details can come in the questions.")
    _ensure_free(home)
    raw = name.strip() if isinstance(name, str) and name.strip() else slug(idea)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", raw):
        raise ShapeError("A name is letters, digits, '.', '_' and '-', and it starts with a letter or digit.")
    name = _unique(home, raw)
    d = _dir(home, name); os.makedirs(d, exist_ok=False)
    sec = _level(security, 1)
    with open(os.path.join(d, "project.env"), "w") as f: f.write(f'WORKFLOW="shape"\nSECURITY_LEVEL="{sec}"\n')
    _save(home, name, {"phase": "describe", "idea": idea, "security": sec, "question_rounds": 0, "questions": [], "history": [], "notes": [],
                       "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
    _spawn(home, name, "Reading what you wrote", lambda: _opening(home, name))
    return {"name": name}

def answer(home, name, answers):
    home = _home(home); _ensure_free(home); st = _clear_dead(home, name, _load(home, name))
    if st.get("phase") != "questions": raise ShapeError("There are no questions waiting.")
    if _busy_live(home, name, st): raise ShapeError("Still reading.", 409)
    answers = answers or {}
    missing = [q for q in st.get("questions") or [] if not str(answers.get(q["id"]) or "").strip()]
    if missing: raise ShapeError("There is still one to answer: " + missing[0]["ask"])
    for q in st["questions"]: q["answer"] = str(answers.get(q["id"]) or "").strip()[:4000]
    _save(home, name, st)
    _spawn(home, name, "Writing the plan", lambda: _plan(home, name))
    return {"ok": True}

def clarify(home, name, text):
    home = _home(home); _ensure_free(home); st = _clear_dead(home, name, _load(home, name))
    if st.get("phase") not in ("ready", "questions"): raise ShapeError("The plan is not at a point where a note can be added.")
    text = str(text or "").strip()
    if not text: raise ShapeError("Write the note first.")
    if len(text) > 8000: raise ShapeError("That note is too long.")
    st.setdefault("notes", []).append(text)
    _save(home, name, st)
    _spawn(home, name, "Writing the plan", lambda: _plan(home, name))
    return {"ok": True}

def retry(home, name):
    home = _home(home); _ensure_free(home); st = _clear_dead(home, name, _load(home, name))
    if _busy_live(home, name, st): raise ShapeError("Still working.", 409)
    kind = st.get("retry") or "plan"
    st["error"] = None; _save(home, name, st)
    fn = {"opening": lambda: _opening(home, name), "review": lambda: run_review(home, name)}.get(kind, lambda: _plan(home, name))
    what = {"opening": "Reading what you wrote", "review": "Reading the code"}.get(kind, "Writing the plan")
    _spawn(home, name, what, fn)
    return {"ok": True}

def set_security(home, name, level):
    home = _home(home); st = _load(home, name)
    if st.get("phase") not in ("describe", "questions", "ready"): raise ShapeError("The security check is chosen before the build starts.")
    st["security"] = _level(level, st.get("security", 1)); _save(home, name, st)
    _env_update(os.path.join(_dir(home, name), "project.env"), {"SECURITY_LEVEL": str(st["security"])})
    return {"security": st["security"]}

def prepare_build(home, name, code_dir, security=None, overrides=None):
    """Write the spec and turn the design into a drafts project. Does not start the loop."""
    home = _home(home); st = _load(home, name)
    if st.get("phase") != "ready" or not (st.get("spec") or "").strip(): raise ShapeError("The plan is not ready to build.")
    if not str(code_dir or "").strip(): raise ShapeError("Choose a folder for the code.")
    code = os.path.realpath(os.path.expanduser(str(code_dir).strip()))
    if code == home or code.startswith(home + os.sep): raise ShapeError("The code folder has to be outside codebot's own folder.")
    if not drafts.coverage(st["spec"])["examples"]:
        raise ShapeError("Nothing in this plan can be checked: it has no examples, so the build would stop at the first version that runs. "
                         "Add a note asking for examples, then update the plan.")
    why = drafts.fits(None, st["spec"], code if os.path.isdir(code) else None)
    if why: raise ShapeError(why)
    sec = _level(security if security is not None else st.get("security", 1))
    clean = {}
    for k, v in (overrides or {}).items():
        if k not in ("DRAFTS_MINUTES", "DRAFTS_SEED", "CODEBOT_MODEL") or str(v).strip() == "": continue
        try: clean[k] = S.validate(k, v)
        except S.SettingError as e: raise ShapeError(str(e))
    os.makedirs(code, exist_ok=True)
    spec = st["spec"] if st["spec"].endswith("\n") else st["spec"] + "\n"
    with open(os.path.join(_dir(home, name), "SPEC.md"), "w") as f: f.write(spec)
    _env_update(os.path.join(_dir(home, name), "project.env"), {"WORKFLOW": "drafts", "WORKDIR": code, "SECURITY_LEVEL": str(sec)})
    for k, v in clean.items(): S.write(home, "project", k, v, name)
    st["phase"] = "building"; st["security"] = sec; st["code_dir"] = code; st["busy"] = None; st["error"] = None
    _save(home, name, st)
    return code

def build(home, name, code_dir, security=None, overrides=None):
    home = _home(home); _ensure_free(home)
    prepare_build(home, name, code_dir, security, overrides)
    cli = os.path.join(home, "bin", "codebot")
    try: p = subprocess.run([cli, "start", name], capture_output=True, text=True, timeout=90, cwd=home, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired: raise ShapeError("The build did not start within 90 seconds.", 504)
    out = (p.stdout + p.stderr).strip()
    if p.returncode != 0:
        st = _load(home, name); st["error"] = out[:800] or "The build did not start."; _save(home, name, st)
        return {"ok": False, "out": out}
    return {"ok": True, "out": out}

# ---------- checklist and the view ----------
def checklist(spec, results, feedback=""):
    """One row per requirement, in the order the spec numbers them. Status comes from the best version's example results when the counts line up."""
    if not (spec or "").strip(): return []
    cov = drafts.coverage(spec); rest = list(results or []); rows, i = [], 0
    unbuilt = bool(feedback) and (feedback.startswith("The code does not compile") or feedback.startswith("These files are missing") or feedback.startswith("Your last reply")
                                  or (feedback.startswith("`") and feedback.split("\n", 1)[0].endswith("failed:")))
    for r in cov["requirements"]:
        n = r["examples"]; chunk = rest[i:i + n] if n else []; i += n
        title = r.get("text") or r["title"] or f"Requirement {r['n']}"
        if not n: rows.append({"n": r["n"], "title": title, "status": "unchecked", "detail": "Nothing in the plan shows an example of this, so the build cannot check it."})
        elif unbuilt: rows.append({"n": r["n"], "title": title, "status": "unbuilt", "detail": "This version did not build, so this was not tried."})
        elif not chunk: rows.append({"n": r["n"], "title": title, "status": "waiting", "detail": ""})
        elif any(c[2] is None for c in chunk): rows.append({"n": r["n"], "title": title, "status": "unbuilt", "detail": "This version did not build, so this was not tried."})
        elif all(c[2] for c in chunk): rows.append({"n": r["n"], "title": title, "status": "passing", "detail": ""})
        else: rows.append({"n": r["n"], "title": title, "status": "failing", "detail": next((c[3] for c in chunk if c[2] is False and c[3]), "")})
    if i != len(rest):
        for row in rows:
            if row["status"] in ("passing", "failing"): row["status"] = "waiting"; row["detail"] = ""
    return rows

def _best_feedback(home, name):
    """The best version's feedback, or None when nothing has built yet. An empty string means every example passed."""
    b = (P.read_json(os.path.join(home, "projects", name, "drafts", "state.json")) or {}).get("best")
    if not b: return None
    return b.get("feedback") or ""

def _live_checklist(home, name, spec):
    fb = _best_feedback(home, name)
    if fb is None: return checklist(spec, [], "")
    return checklist(spec, P.example_results(home, name, fb), fb)

def _score(rows):
    checked = [r for r in rows if r["status"] != "unchecked"]
    return {"passing": sum(1 for r in checked if r["status"] == "passing"), "total": len(checked)}

def _legacy(home, name):
    d = os.path.join(home, "projects", name); spec = P.read_text(os.path.join(d, "SPEC.md")) or ""
    st = P.state(home, name); dr = st.get("drafts") or {}; fin = dr.get("finished") or {}
    rows = _live_checklist(home, name, spec)
    phase = "done" if fin else ("building" if dr.get("started") else "ready")
    words = {"examples pass": "Every check passed.", "time cap": "It ran out of time.", "round cap": "It reached its round limit.", "model errors": "The model stopped answering."}
    notice = words.get(fin.get("stop"), "The build is finished.") if fin else ""
    if fin.get("stop") == "examples pass" and not dr.get("examples_total"): notice = "It stopped at the first version that built: nothing in the plan could be checked, so nobody has confirmed it works."
    return {"plain": True, "phase": phase, "idea": "", "understood": "", "questions": [], "outline": None, "spec": spec,
            "security": _level(read_env(os.path.join(d, "project.env")).get("SECURITY_LEVEL"), None) if read_env(os.path.join(d, "project.env")).get("SECURITY_LEVEL") else None,
            "suggest_security": None, "code_dir": st.get("workdir") or "", "busy": None, "error": None, "review": None, "notice": notice,
            "checklist": rows, "score": _score(rows), "build_state": st["state"], "open": []}

def view(home, name):
    home = _home(home)
    d = os.path.join(home, "projects", name)
    if not os.path.isfile(os.path.join(d, "project.env")): raise ShapeError("no such project", 404)
    if not os.path.isfile(_path(home, name)):
        if read_env(os.path.join(d, "project.env")).get("WORKFLOW") == "drafts": return _legacy(home, name)
        raise ShapeError("no design for this project", 404)
    st = _clear_dead(home, name, _load(home, name))
    spec = st.get("spec") or P.read_text(os.path.join(d, "SPEC.md")) or ""
    env = read_env(os.path.join(d, "project.env"))
    rows = _live_checklist(home, name, spec) if spec else []
    ps = P.state(home, name)
    return {"plain": False, "phase": st.get("phase") or "describe", "idea": st.get("idea") or "", "understood": st.get("understood") or "",
            "questions": [{k: q.get(k, "") for k in ("id", "ask", "why", "answer")} for q in st.get("questions") or []],
            "outline": st.get("outline"), "spec": spec, "security": st.get("security", 1), "suggest_security": st.get("suggest_security"),
            "code_dir": st.get("code_dir") or ps.get("workdir") or os.path.join(os.path.expanduser("~"), "code", name),
            "busy": (st.get("busy") or {}).get("what") if _busy_live(home, name, st) else None, "error": st.get("error"),
            "review": st.get("review"), "notice": st.get("notice") or "", "checklist": rows, "score": _score(rows),
            "build_state": ps["state"] if env.get("WORKFLOW") == "drafts" else "shaping",
            "open": (st.get("outline") or {}).get("open") or []}

# ---------- the closing review, run by the drafts loop ----------
def _serious(name):
    n = (name or "").strip().lower()
    if n in ("serious", "high", "critical"): return "serious"
    if n in ("real", "medium"): return "real"
    return "low"

def _findings(reply):
    out = []
    for f in (reply.get("findings") or [])[:20]:
        if not isinstance(f, dict): continue
        what = str(f.get("what") or "").strip()
        if what: out.append({"where": str(f.get("where") or "").strip()[:120] or "the program", "what": what[:500], "serious": _serious(f.get("serious"))})
    return out

def _sources(proj, limit=40000):
    root = proj.rundir if proj.rundir and os.path.isdir(proj.rundir) else proj.workdir
    if not root or not os.path.isdir(root): return ""
    chunks, used = [], 0
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in (".git", "__pycache__", "node_modules", ".gocache") and not d.startswith("."))
        for f in sorted(fn):
            if not (f.endswith(drafts.SOURCE_EXT) or f in drafts.CONFIG_FILES): continue
            p = os.path.join(dp, f)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh: text = fh.read(12000)
            except OSError: continue
            if "\0" in text[:200]: continue
            rel = os.path.relpath(p, root); block = f"### {rel}\n{text}\n"
            if used + len(block) > limit: return "".join(chunks) + "\n(further files omitted)\n"
            chunks.append(block); used += len(block)
            if len(chunks) >= 40: return "".join(chunks)
    return "".join(chunks)

def _notice(review, level, stop, total=None):
    lead = "The build is finished."
    if stop == "examples pass" and total == 0: lead = "It stopped at the first version that built: nothing in the plan could be checked, so nobody has confirmed it works."
    if stop and stop != "examples pass":
        lead = {"time cap": "The build ran out of time.", "round cap": "The build reached its round limit.",
                "model errors": "The build stopped because the model stopped answering."}.get(stop, "The build stopped before every check passed.")
    parts = [lead]
    sec = review.get("security") or {}
    if level == 0: parts.append("You asked for no security check.")
    elif sec.get("summary"): parts.append(sec["summary"])
    code = review.get("code") or {}
    if code.get("summary"): parts.append(code["summary"])
    if review.get("again"): parts.append("The serious notes were read a second time.")
    return " ".join(parts)[:700]

def run_review(home, name):
    """Security pass (unless level 0), then a code pass, then a second look when something was called serious. Returns the sentence to show the person."""
    home = _home(home)
    st = _load(home, name) if os.path.isfile(_path(home, name)) else None
    if st and st.get("phase") == "done" and st.get("notice") and st.get("review"): return st["notice"]     # a finished review; a failed one is run again
    proj = Project(name, home)
    level = _level(proj.get("SECURITY_LEVEL", (st or {}).get("security", 1)))
    if st:
        st["phase"] = "review"; st["busy"] = {"pid": os.getpid(), "what": "Reading the code", "since": time.time()}; st["error"] = None; _save(home, name, st)
    try:
        code = _sources(proj)
        ps = P.state(home, name); stop = ((ps.get("drafts") or {}).get("finished") or {}).get("stop"); total = (ps.get("drafts") or {}).get("examples_total")
        if not code.strip():
            review = {"security": {"summary": "No code was produced, so there was nothing to review.", "findings": [], "level": level},
                      "code": {"summary": "No code was produced, so there was nothing to review.", "findings": []}}
        else:
            review = {}
            if level == 0: review["security"] = {"summary": "No security check, as you asked.", "findings": [], "level": 0}
            else:
                got = _call(proj, security_brief(level), "Read this code and report only what the instructions ask for.\n\n" + code, _REVIEW, 900)
                review["security"] = {"summary": str(got.get("summary") or "").strip()[:500], "findings": _findings(got), "level": level}
            got = _call(proj, CODE_BRIEF, "Read this code and report only basic defects.\n\n" + code, _REVIEW, 900)
            review["code"] = {"summary": str(got.get("summary") or "").strip()[:500], "findings": _findings(got)}
            serious = [f for f in review["security"]["findings"] + review["code"]["findings"] if f["serious"] == "serious"]
            if serious:
                again = _call(proj, "You flagged the notes below as serious. Read the code again and keep a finding only if the code actually does that. Drop anything you are unsure about. " + CODE_BRIEF,
                              code + "\n\nNotes:\n" + "\n".join(f"- {f['where']}: {f['what']}" for f in serious), _REVIEW, 700)
                review["again"] = {"summary": str(again.get("summary") or "").strip()[:500], "findings": _findings(again)}
        notice = _notice(review, level, stop, total)
        if st is not None or os.path.isfile(_path(home, name)):
            cur = _load(home, name); cur["review"] = review; cur["notice"] = notice; cur["phase"] = "done"; cur["busy"] = None; cur["error"] = None; _save(home, name, cur)
        return notice
    except Exception as e:
        notice = "The build finished. The closing review did not finish: " + _friendly(e)
        if os.path.isfile(_path(home, name)):
            cur = _load(home, name); cur["phase"] = "done"; cur["busy"] = None; cur["error"] = _friendly(e); cur["notice"] = notice; cur["retry"] = "review"; _save(home, name, cur)
        return notice

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m codebot.shape")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review"); r.add_argument("name"); r.add_argument("--home", required=True)
    a = ap.parse_args(argv)
    print(run_review(a.home, a.name)); return 0

if __name__ == "__main__":
    sys.exit(main())
