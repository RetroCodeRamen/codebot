# codebot

Local-only ralph-loop coding agent. No cloud compute.

- Model: `ornith:9b` (Q4, 5.6 GB, agentic-coding tuned), 32k context, CPU inference (~6 tok/s on this machine)
- Harness: opencode (`tools/`) with a lean custom agent (`codebot`, ~3k-token prompt instead of 11k); private Ollama 0.40.3 on port 11500 (`tools/ollama`, models in `models/`)
- Notifications: ntfy topic in `config/codebot.env` (subscribe to it in the ntfy app)

## Easiest way: just run `codebot`
Interactive harness: pick/create a project -> planning chat -> "start the loop?" -> live view that streams the
agent's thinking, every file read/edit and command. In the live view: `p` pause/resume, `a` answer, `s` status,
`x` stop, `q` detach (loop keeps running; run `codebot` again to reattach).

## CLI
```
codebot plan <name> [workdir]   planning chat -> projects/<name>/PLAN.md + verify.sh
codebot start <name>            autonomous loop in background
codebot pause <name> [--soft]   stop now & unload model (frees ~7 GB RAM + CPU); --soft = after this iteration
codebot resume <name>
codebot answer <name> "text"    reply to a BLOCKED push
codebot status [name] | log <name> | stop <name>
codebot diff <name>             what codebot changed + the merge command
codebot tidy                    condense memory/lessons.md
codebot setup | notify-test
```

## How the loop works
Each iteration = a fresh opencode session that reads memory + PLAN.md + PROGRESS.md + ANSWERS.md, does the
first unchecked task, verifies it, checks it off, commits. State lives in files, so pausing/crashing loses at
most one iteration. The agent writes `DONE` or `BLOCKED: ...` to `projects/<name>/STATUS`; both send an ntfy push.

Safety nets:
- **Git isolation**: if the target is an existing repo, work happens in a separate git worktree
  (`projects/<name>/work`, branch `codebot/<name>`). Your checkout and uncommitted changes are never touched.
  Review with `codebot diff <name>`, then merge yourself. Brand-new folders are worked in place.
- **Verify gate**: `projects/<name>/verify.sh` (written during planning) must exit 0 before DONE is accepted.
  If it fails, a fix task is added to the plan (3 failures -> BLOCKED ping).
- **Stall detection**: 3 iterations with no change -> BLOCKED ping. Per-iteration timeout 45 min. Iteration cap 300.
- Finished iterations are ended automatically 20 s after STATUS appears (the small model likes to ramble).

## Memory / expertise
`memory/expertise.md` (curated, edit freely) and `memory/lessons.md` (agent appends; auto-condensed when over
`LESSONS_MAX` bytes, backups in `memory/.archive/`). Config/prompts/memory are git-versioned.

## Notes
- The agent has full shell access as you (sudo and git push are denied in config/opencode.json).
- GPU: no NVIDIA kernel driver installed. `sudo apt install nvidia-driver-580-open` + reboot lets Ollama split
  layers onto the 4 GB card automatically.
- RAM is tight (15 GB). After a pause/cold start the first request takes a minute or so (model reload).
