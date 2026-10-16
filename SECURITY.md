# Security

codebot runs code that a language model writes, unattended. This file says what it protects against, what it does not, and how to report a problem.

## What is protected

- **The model's commands run in a sandbox.** Every `run` action and every acceptance check executes in a throwaway Docker container with
  `--network none`, a read-only root, all capabilities dropped, `no-new-privileges`, memory and process limits, and only the project's code
  directory writable. Your home directory and the harness state (`projects/<name>/`, the frozen checks) are not mounted.
- **The model cannot change how it is judged.** Acceptance checks are frozen (sha256 + a frozen copy) before work starts; a worker that edits a
  check, a fixture or `tasks.json` is detected, the change is undone and the attempt fails.
- **Your repository is not edited in place.** Existing git repos are worked on in a separate worktree and branch.
- **Nothing leaves the machine by default.** The model runs on a private local Ollama (127.0.0.1). Notifications are off by default; when on,
  every outbound request the harness makes is logged in `logs/egress.jsonl`.
- **Evidence.** `audit.jsonl` is a hash-chained record of commands, file writes, model requests and check runs (`codebot audit --probe`).

## What is not protected

- **Membership of the `docker` group is root-equivalent.** codebot needs it to start containers. The model never gets it, but any bug in the
  harness that let model output reach a `docker` command line would be serious. Please report one if you find it.
- **The harness itself runs outside the sandbox.** The engine, worker loop and planner are trusted Python and bash code that read model output
  (JSON) and decide what to do with it. They validate paths, actions and schemas, but they are not isolated.
- **`SANDBOX=none` or `SANDBOX=auto` without Docker runs commands directly on your machine.** The audit log records this. Do not use it with code
  or specs you do not trust.
- **ntfy topics are public by name.** With `NTFY_ENABLED=1` anyone who learns your topic can read your notifications; with `PHONE_CONTROL=1` they
  can also pause or stop runs and send answers to blocked tasks, and those answers become guidance in the model's prompt. Use a long random
  topic (the installer makes one), keep it out of anything you publish, or use a protected topic with `NTFY_TOKEN`.
- **The audit log is tamper-evident, not tamper-proof.** Someone with your user account can rewrite it.
- **Specs and repositories are trusted input.** A malicious spec or repo can try to steer the model (prompt injection). The sandbox limits what
  the resulting commands can do, not what code ends up in your worktree; review `codebot diff` before merging.

## Reporting

Open a GitHub issue for anything that is not sensitive. For a sandbox escape or a way to make the harness run something outside the sandbox,
please use GitHub's private vulnerability reporting on this repository instead of a public issue.
