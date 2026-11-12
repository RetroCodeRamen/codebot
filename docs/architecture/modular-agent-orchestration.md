# Modular agent orchestration (pipeline graph)

**Status:** Milestones 1–7 core implemented (schemas, sequential scheduler, messaging, scopes/artifacts, Team Designer, SVG team/workflow canvas + JSON sync, GUI monitor). Drafts bridge (`action: drafts`), debate + small-pods templates, pod manager helpers. Scratch/Blockly not built; the SVG canvas edits `can_talk_to` and layout.
**North star:** the user’s modular team plan (Elephant/Goldfish planning, configurable workers, pods, JSON + connections GUI), as expanded in the Cursor implementation prompt of 2026-11-11.
**Constraint that never moves:** only one model inference at a time on this machine (`lib/codebot/jobs.py` + per-call `logs/model.lock`). Agents talk through durable messages and a harness scheduler, never by overlapping live sessions.

## 1. Repository assessment (what already exists)

| Need | Already in codebot | Gap |
|---|---|---|
| One inference at a time | `jobs.Hold` / `model-job.lock`; bash `flock logs/model.lock` per step | Scheduler that owns multi-agent READY/WAITING, not only “one project vs another” |
| Local Ollama | `lib/codebot/ollama.py` (`chat`, structured `format`, streaming stop) | Per-agent model/params from pipeline JSON |
| Single-agent build | Drafts (`WORKFLOW=drafts`, `drafts.py`); shape design chat (`shape.py`) | Optional multi-node workflow without replacing drafts |
| Task pipeline (legacy) | `engine.py`, frozen checks, ladder, worker actions | Kept working; not the place to grow teams |
| Modes / prompts | `modes/<name>/` | Agent *templates* are a new layer; modes stay for sandbox/tool policy |
| Memory | `memory/` lessons + per-project `MEMORY_DIR` | Per-agent memory policy (none / task / project / persistent) |
| Checkpoints | git worktrees, drafts round state, `tasks.json` | Pipeline run state: queue, messages, agent WAITING |
| Sandbox / permissions | Docker sandbox, path checks in worker | Per-agent read/write scopes enforced on every tool call |
| GUI | `lib/codebot/gui/` (vanilla ES, 127.0.0.1) | Later: team canvas + workflow blocks + JSON sync; not Milestone 1 |
| Acceptance | SPEC examples (drafts); frozen checks (tasks) | Pipeline completion still needs a **mechanical** gate, not model self-approval |

**Reuse first.** Do not rewrite drafts, shape, ollama, jobs, or the GUI shell. Add `WORKFLOW=pipeline` beside them. A one-node pipeline that runs today’s drafts loop is the compatibility bridge.

## 2. What the ChatGPT outline got right

1. **JSON is canonical**; any GUI is a view of the same model.
2. **Roles are not hard-coded** (no forced architect / pod manager).
3. **Sequential inference with WAITING + structured Q&A** matches laptop heat and `jobs.py`.
4. **Separate agent / team / workflow / project configs** with inheritance.
5. **Harness owns the scheduler**; the model does not pick who runs next as free prose.
6. **Fresh reviewers** = memory policy `none` or `invocation` without prior review chat.
7. **Team Designer emits validated JSON** the user edits before run.

## 3. What we deliberately cut or defer (so this ships)

| Outline item | Decision |
|---|---|
| Scratch + Blockly + node editor + JSON in one go | **Defer GUI to Milestone 6.** Milestone 1–5 are engine + JSON. A thin GUI can load/edit JSON first; Scratch/Blockly is last. |
| Full Team Designer as an always-on product | Ship as an **optional agent template + workflow**, after planning/review works. |
| Nested teams, pods, managers | Supported: `team.teams` + member `"team"` expands to `parent-child` ids and pods; managers get RESULT on worker finish. |
| Parallel fan-out inference | Fan-out = **queue**, never concurrent model calls (already in the outline; keep it). |
| Replacing drafts as the default | **No.** Drafts remains the recommended simple path. Pipeline is opt-in per project. |
| Model self-approval as done | **Forbidden.** Done = examples / frozen checks / user approval gates in the workflow. |
| New frameworks (Blockly npm, etc.) | Avoid. GUI stays vanilla ES modules like today, or SVG canvas + JSON. |

## 4. Target shape (component structure)

```
pipelines/templates/          # shipped agent/team/workflow examples (JSON)
projects/<name>/
  project.env                 # WORKFLOW=pipeline, PIPELINE=...
  pipeline/
    project.json              # binds templates + overrides
    run.json                  # durable scheduler state (M2+)
    messages.jsonl            # agent mailbox log (M3+)
    artifacts/                # versioned refs (M4+)
lib/codebot/pipeline/         # parse, validate, later: schedule, message, invoke
lib/codebot/jobs.py           # global inference ownership (extended kinds)
lib/codebot/ollama.py         # unchanged call shape; caller passes model/params
```

**Data flow (steady state):**

1. User (or Team Designer) produces validated `project.json` → team + workflow refs.
2. Scheduler loads workflow, puts start node’s agent in READY.
3. At most one agent is RUNNING: harness builds that agent’s context from identity + task + allowed messages + `sees` paths, calls Ollama, expects a **structured action** (edit artifact, ask, finish, fail).
4. Blocking QUESTION → agent WAITING, message queued, recipient READY; inference lock free between calls.
5. Mechanical gates decide advance / retry / stop. The harness runs them itself on a scratch copy of the workspace (`lib/codebot/pipeline/gates.py`): `examples` (the spec's build command and worked examples, judged as drafts mode judges a version), `tests` (`verify.sh` in the sandbox, exit status 0), `spec-examples` (the spec has runnable examples; for spec-writing workflows, before code exists), `approve` (the user). A failed gate follows only a `when: fail` edge, at most `max_retries` times (default 2); otherwise the run is FAILED with the check's output in `run.json` under `gates`. A check that is unknown or cannot be run fails.
6. On crash: `run.json` + message log restore; completed step IDs are not replayed.

## 5. Configuration format (Milestone 1)

Four document kinds, all `"schema": "codebot.pipeline/v1"`:

| Kind | File role |
|---|---|
| `agent` | Reusable template: id, goal, instructions, model, memory, sees (read/write), can_talk_to, message types, limits |
| `team` | Instances of agents, optional pods/hierarchy, overrides |
| `workflow` | Nodes (steps) + edges (handoff / fan-out-queue / join / gate / loop with bound) |
| `project` | Selects team + workflow + workspace + limits |

**Connection kinds** (team + workflow edges):

- `agent` — message / Q&A only  
- `repository` — read/write under scoped paths  
- `both` — messaging and repo access  

Layout-only GUI fields (`x`, `y`, `color`) live under `"ui": {...}` and must round-trip without affecting execution.

Validators (stdlib, no JSON Schema library): duplicate IDs, missing refs, talk-to not in team, write∉read, unbounded loops, unreachable nodes, schema version.

## 5b. What an agent can do in one turn (`pipeline/tools.py`, `invoke.py`)

One turn is one model call that returns one JSON action. The schema offered to the model lists only the actions that agent may take at that
moment, and the system prompt explains each of them.

| Action | Who has it | What the harness does |
|---|---|---|
| `list`, `read` | `sees.read` and the tool (default) | the files it may read; one file, or an artifact (`artifacts/NAME`), cut at 12,000 characters |
| `write` | `sees.write` | replaces one file inside its write globs (whole text, one file per turn) |
| `check` | the tool (default) | runs the spec's worked examples on a copy of the workspace: the examples gate's own judgement |
| `run` | `"tools"` lists `run` | one shell command in the workspace, in the sandbox, under the worker's command policy |
| `ask`, `escalate`, `answer` | `can_talk_to` / a pending question | blocking question and its answer (section 6) |
| `findings`, `finish`, `fail` | everyone | end the agent's part; `fail` stops the run |

An agent's `"tools"` is a list of `read`, `list`, `check`, `run`; without it the agent gets `read`, `list`, `check`.

**Turn memory.** A context is rebuilt for every call, so every action and its result is appended to `pipeline/turns.jsonl` and the agent's own
turns of the current visit of its node are shown at the end of its next prompt (the last three results whole, older ones shortened; `project`
and `persistent` memory add one line per turn of earlier visits). A request that cannot be carried out (a path outside `sees`, a missing file,
an action it has no right to, a command the policy denies, a reply that is not JSON or was cut off, the same action twice in a row) is a turn
whose result is the error, not a failed run. After `limits.max_turns` turns (default 16) on one visit the node ends without a model call, the
run records a warning, and whatever checks the work decides. When a gate fails, the agent the fail edge returns to is shown the check's output.

**Team Designer** (`design.propose`, `teamgen.py`). The model answers one constrained question: the workers, the files each writes, which
workers each builds on, whether a coordinator and a QA reviewer are needed. The harness writes the agents, team and workflow from the answer, so
the result is always valid: ids are repaired, a file has one owner, a file the spec names that nobody took gets one, a worker with no file is
dropped. Two or more workers become `start → [coordinator writes PLAN.md] → each worker in dependency order → [QA findings] → integrator → examples
gate (fail → integrator, twice) → end`; a spec without runnable examples ends in an approval instead. One usable worker, or nothing usable, is
the solo template (the drafts loop). Every agent uses the machine's configured model; the designer does not assign models, and it is one model
call, not a member of the team that messages others.

## 6. Execution lifecycle (later milestones)

States: `READY | RUNNING | WAITING | SUSPENDED | COMPLETED | FAILED | CANCELLED`.

Handoffs only at **structured action boundaries** (after a chat returns). Never mid-token.

Nested A→B→C→B→A is a stack of WAITING + correlation IDs; deadlock = circular wait with no READY agent → FAILED + escalate.

## 7. GUI

Same `codebot gui` server. Route `#/pipeline/<name>`: team SVG canvas (links agent / repository / both + layout), workflow graph, JSON editor, live monitor (`run.json` / messages / artifacts). Validate before save. Cancel via API/CLI.

Scratch/Blockly remains deferred; JSON is still the source of truth.

## 7b. Acceptance scenarios (fake-model tests)

| ID | Scenario | Coverage |
|---|---|---|
| A | Single developer wraps drafts | `basic-calculator` + drafts bridge test |
| B | Coordinator + pods | `small-pods` template + pod helpers |
| C | Architect + goldfish reviewers | `spec-with-reviewers` fan-out test |
| D | Goldfish debate (Q&A between reviewers) | `spec-debate` ask/answer test |

## 8. Integration points

| Existing | How pipeline hooks in |
|---|---|
| `codebot new` / drafts | Unchanged. Optional: “upgrade to pipeline” later. |
| `codebot gui` shape → Build | Keep. Optional pre-Build goldfish becomes a **workflow template** (architect + reviewers), not a rewrite of shape. |
| `bin/codebot start` | If `WORKFLOW=pipeline`, supervisor calls `codebot-engine pipeline-step` (M2) instead of `loop_drafts`. |
| `jobs.py` | Add kind `pipeline` (or reuse `project`) so GUI/CLI refuse a second model job. |
| Acceptance | Drafts examples runner and/or project `verify` / gate nodes in the workflow. |

## 9. Milestones (dependency order)

1. **Architecture + JSON** — done  
2. **Sequential scheduler** — done (one lock, checkpoints, restart)  
3. **Messaging + memory policies** — done (QUESTION/ANSWER, nested A-B-C, none/task/project/persistent)  
4. **Workspaces + artifacts** — done (`sees`, artifact refs)  
5. **Planning + Team Designer** — done (`pipeline design|init`, templates; since 2026-11-11 the designer writes a team from the spec, section 5b)  
6. **Visual editor** — done (SVG team/workflow canvas + JSON; Scratch deferred)  
7. **Monitor + full GUI integration** — done (live status, cancel, approve)  

Acceptance A–D covered by fake-model tests. Real-model success-rate A/B vs drafts is the next measurement step, not a missing feature.

## 10. Design rules carried from codebot

- Local only; no new network dependencies.  
- Harness decides pass/fail.  
- Extend before rewrite; drafts and `codebot plan` keep working.  
- Stdlib Python in `lib/`.  
- Measure on held-out tasks before claiming team graphs improve success rate (`docs/dev/REVIEW-2026-10-21.md`).  
- Prefer a thin graph that wraps drafts over a large org chart that never finishes on a 15 GB laptop.
