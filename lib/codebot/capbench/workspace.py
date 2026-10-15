"""Trial workspaces. Every trial gets its own harness state dir (projects/<name>) and its own code dir (bench/runs/<name>/code, a fresh git repo seeded from
the catalog), so repeated trials start from identical conditions and nothing a worker does can reach the codebot source tree, the catalog or another trial.
The worker only ever sees its code dir (the Docker sandbox mounts nothing else); the reference tests stay in the catalog."""
import json, os, shutil, subprocess
from .. import bench
from ..config import Project

def _git(d, *a): return bench._git(d, *a)

def names(run_id, trial_id): return f"cap-{run_id}-{trial_id}".lower().replace("_", "-")

def env_lines(cfg):
    return "".join(f'{k}="{v}"\n' for k, v in cfg.items() if v is not None and v != "")

def project_settings(home, task_minutes, model=None, seed=None, interactive=False, extra=None, minimal=False):
    """project.env for a benchmark trial. These are the ONLY knobs the benchmark sets; everything else (ladder tries, stuck signals, thermal guard, context)
    is codebot's normal configuration, because the point is to measure the real harness."""
    cfg = {"MODE": "code", "BENCH": "1", "CAPBENCH": "1", "NTFY_ENABLED": "0", "PHONE_CONTROL": "0",          # the benchmark runner (not the loop) decides what is worth a notification
           "SANDBOX": "docker",                                                                                # hard requirement: the worker must not be able to read the hidden reference tests
           "WORKER": "native", "CHECK_BASELINE_GUARD": "1",
           "TASK_MAX_MINUTES": f"{task_minutes:g}", "BLOCK_TERMINAL": "0" if interactive else "1",
           "MAX_ITERATIONS": "0", "PROJECT_MAX_MINUTES": "0",                                                  # no project-wide limit of any kind
           "CODEBOT_MODEL": model, "MODEL_SEED": seed}
    if minimal: cfg.update({"LADDER_TRIES": "1,0,0,0,0", "TASK_MAX_ATTEMPTS": "1", "WORKER_MAX_TURNS": "400", "ALLOW_UNCHECKED": "1", "CHECK_BASELINE_GUARD": "0"})
    cfg.update(extra or {}); return cfg

def create(home, name, seed_dirs, spec_text, cfg):
    """State dir + code dir (+ git baseline). Refuses to touch an existing project. Returns the Project."""
    state = os.path.join(home, "projects", name); code = os.path.join(home, "bench", "runs", name, "code")
    if os.path.exists(state) or os.path.exists(code): raise SystemExit(f"project {name} already exists; benchmark trials never reuse a workspace")
    os.makedirs(code)
    for d in seed_dirs:
        if d and os.path.isdir(d): shutil.copytree(d, code, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    if not os.listdir(code): open(os.path.join(code, ".gitkeep"), "w").close()
    _git(code, "init", "-q", "-b", "main"); _git(code, "add", "-A"); _git(code, "commit", "-qm", "seed")
    os.makedirs(state)
    with open(os.path.join(state, "project.env"), "w") as f: f.write(f'WORKDIR="{code}"\n' + env_lines(cfg))
    for fn in ("PROGRESS.md", "ANSWERS.md"): open(os.path.join(state, fn), "w").close()
    with open(os.path.join(state, "SPEC.md"), "w") as f: f.write(spec_text)
    return Project(name, home)

def code_dir(proj):
    """Where the finished code is: the worktree `codebot start` created (RUNDIR) once started, else the seeded dir."""
    return proj.rundir if proj.rundir and os.path.isdir(proj.rundir) else proj.workdir

def destroy(home, name, keep_state=False):
    """Clean reset of a trial: remove the code dir, the worktree, the git branch and (unless asked) the state dir. Only ever touches this trial's own paths."""
    proj = None
    try: proj = Project(name, home)
    except SystemExit: pass
    if proj:
        wt = os.path.join(home, "workspaces", name)
        if os.path.isdir(wt): subprocess.run(["git", "-C", proj.workdir, "worktree", "remove", "--force", wt], capture_output=True); shutil.rmtree(wt, ignore_errors=True)
    runs = os.path.join(home, "bench", "runs", name)
    if os.path.isdir(runs) and os.path.realpath(runs).startswith(os.path.realpath(os.path.join(home, "bench", "runs")) + os.sep): shutil.rmtree(runs, ignore_errors=True)
    if not keep_state:
        st = os.path.join(home, "projects", name)
        if os.path.isdir(st) and os.path.realpath(st).startswith(os.path.realpath(os.path.join(home, "projects")) + os.sep): shutil.rmtree(st, ignore_errors=True)
