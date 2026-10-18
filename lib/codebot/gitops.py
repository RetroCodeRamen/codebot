"""Git checkpoints in the code dir. A baseline commit captures everything that already exists, so a reset can
never delete pre-existing user files; a verified task = a commit + a tag; retry-fresh resets to the last good one."""
import hashlib, json, os, subprocess
from .config import now

_ID = ["-c", "user.name=codebot", "-c", "user.email=codebot@localhost", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null"]

def git(rundir, *args, check=False):
    r = subprocess.run(["git", "-C", rundir, *_ID, *args], capture_output=True, text=True)
    if check and r.returncode: raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r

def is_repo(rundir): return git(rundir, "rev-parse", "--git-dir").returncode == 0
def head(rundir):
    r = git(rundir, "rev-parse", "HEAD"); return r.stdout.strip() if r.returncode == 0 else None
def ensure_repo(rundir):
    if not is_repo(rundir): git(rundir, "init", "-q", check=True)

def commit_all(rundir, msg):
    """Stage everything (respecting .gitignore) and commit. Returns the resulting HEAD sha (existing HEAD if clean)."""
    git(rundir, "add", "-A")
    if git(rundir, "diff", "--cached", "--quiet").returncode != 0 or head(rundir) is None:
        git(rundir, "commit", "-q", "--no-verify", "--allow-empty", "-m", msg, check=True)
    return head(rundir)

def _cp_path(proj): return proj.p("checkpoints.json")
def load(proj):
    try:
        with open(_cp_path(proj)) as f: return json.load(f)
    except (OSError, ValueError): return {"baseline": None, "list": []}
def _save(proj, cp):
    with open(_cp_path(proj), "w") as f: json.dump(cp, f, indent=1)

def ensure_baseline(proj):
    cp = load(proj)
    if cp["baseline"] and git(proj.rundir, "cat-file", "-e", cp["baseline"]).returncode == 0: return cp["baseline"]
    ensure_repo(proj.rundir)
    cp["baseline"] = commit_all(proj.rundir, "codebot: baseline (everything that existed before the run)")
    _save(proj, cp); return cp["baseline"]

def last_good(proj):
    cp = load(proj); return cp["list"][-1]["sha"] if cp["list"] else cp["baseline"]

def checkpoint(proj, task):
    """After a verified task: commit the work, tag it, record it."""
    sha = commit_all(proj.rundir, f"codebot: {task['id']} {task['title']}"[:200])
    tag = f"codebot/{proj.name}/{task['id']}"
    git(proj.rundir, "tag", "-f", tag, sha)
    cp = load(proj); cp["list"].append({"task": task["id"], "sha": sha, "tag": tag, "ts": now()}); _save(proj, cp)
    return sha

def shelve(proj, path):
    """Before a reset that ends a task's work: write the code dir's changes since the last verified checkpoint (changed and new files; ignored ones,
    such as a built program or a build cache, are not in it) to path as a patch `git apply` restores. Returns the number of files in it (0: nothing
    was written)."""
    base = last_good(proj)
    if not base: return 0
    git(proj.rundir, "add", "-A")                    # new files too; the reset that follows clears the index again
    names = [n for n in git(proj.rundir, "diff", "--cached", "--name-only", "-z", base).stdout.split("\0") if n]
    if not names: return 0
    r = subprocess.run(["git", "-C", proj.rundir, *_ID, "diff", "--cached", "--binary", base], capture_output=True)    # bytes: any file encoding
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(r.stdout)
    return len(names)

def reset_to_last_good(proj):
    """Hard-reset tracked files and remove untracked, non-ignored files created since. Never goes below baseline."""
    sha = last_good(proj)
    if not sha: return None
    git(proj.rundir, "reset", "-q", "--hard", sha, check=True)
    git(proj.rundir, "clean", "-fdq")          # no -x: ignored files (venvs, caches) are left alone
    return sha

def changed_files(rundir, limit=40, max_bytes=1_000_000):
    """{relative path: sha1} for files changed or created since HEAD (used for edit ping-pong detection)."""
    r = git(rundir, "status", "--porcelain", "-z", "--untracked-files=all")
    out = {}
    for ent in r.stdout.split("\0"):
        if len(ent) < 4: continue
        path = ent[3:]
        full = os.path.join(rundir, path)
        try:
            if os.path.isfile(full) and os.path.getsize(full) <= max_bytes:
                with open(full, "rb") as f: out[path] = hashlib.sha1(f.read()).hexdigest()[:12]
            else: out[path] = "gone"
        except OSError: out[path] = "gone"
        if len(out) >= limit: break
    return out
