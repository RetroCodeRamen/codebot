"""python3 -m codebot.pipeline validate|step|run|status|design ..."""
import json, os, sys

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python3 -m codebot.pipeline validate <file.json>...\n"
              "       python3 -m codebot.pipeline init <project> [--template NAME] [--force] [--home DIR]\n"
              "       python3 -m codebot.pipeline step <project> [--home DIR]\n"
              "       python3 -m codebot.pipeline run <project> [--home DIR] [--max N]\n"
              "       python3 -m codebot.pipeline status <project> [--home DIR]\n"
              "       python3 -m codebot.pipeline cancel <project> [--home DIR]\n"
              "       python3 -m codebot.pipeline approve <project> [--home DIR]\n"
              "       python3 -m codebot.pipeline design <project> [--home DIR] [--spec PATH] [--template NAME]", file=sys.stderr)
        return 2
    cmd = argv[0]; args = argv[1:]
    if cmd == "validate":
        from .load import load_and_validate
        from .validate import ValidationError
        if not args: return main(["--help"])
        rc = 0
        for p in args:
            try: load_and_validate(p); print(f"OK {p}")
            except (OSError, ValueError, ValidationError) as e:
                rc = 1
                from .validate import ValidationError as VE
                if isinstance(e, VE):
                    print(f"FAIL {p}", file=sys.stderr)
                    for err in e.errors: print(f"  - {err}", file=sys.stderr)
                else: print(f"FAIL {p}: {e}", file=sys.stderr)
        return rc
    home = None
    if "--home" in args:
        i = args.index("--home"); home = args[i + 1]; del args[i:i + 2]
    if cmd in ("step", "run", "status", "design", "cancel", "init", "approve"):
        if not args: return main(["--help"])
        from codebot.config import Project
        name = args[0]
        proj = Project(name, home)
        if cmd == "status":
            from . import runtime as R, mailbox, artifacts
            run = R.load_run(proj)
            print(json.dumps({
                "run": run,
                "messages": list(mailbox.iter_messages(R.pipeline_dir(proj)))[-20:] if run else [],
                "artifacts": artifacts.list_artifacts(R.pipeline_dir(proj)) if run else []
            }, default=str))
            return 0
        if cmd == "cancel":
            from . import schedule
            print(json.dumps(schedule.cancel(proj), default=str)); return 0
        if cmd == "approve":
            from . import schedule, runtime as R
            ev = schedule.approve(proj)
            out = {k: v for k, v in ev.items() if k != "run"}; out["run"] = ev.get("run") or R.load_run(proj)
            print(json.dumps(out, default=str)); return 0
        if cmd == "init":
            from . import design
            from .validate import ValidationError
            tmpl = "solo"; force = False
            if "--template" in args:
                i = args.index("--template"); tmpl = args[i + 1]; del args[i:i + 2]
            if "--force" in args:
                force = True; args.remove("--force")
            try:
                path, meta = design.init_project(proj, tmpl, force=force)
            except ValidationError as e:
                print(json.dumps({"ok": False, "errors": e.errors})); return 1
            print(json.dumps({"ok": True, "path": path, "meta": meta})); return 0
        if cmd == "design":
            from . import design
            spec = None; tmpl = None
            if "--spec" in args:
                i = args.index("--spec")
                with open(args[i + 1], encoding="utf-8") as f: spec = f.read()
                del args[i:i + 2]
            if "--template" in args:
                i = args.index("--template"); tmpl = args[i + 1]; del args[i:i + 2]
            if spec is None:
                for p in (proj.p("SPEC.md"), os.path.join(proj.workdir or "", "SPEC.md")):
                    if p and os.path.isfile(p):
                        with open(p, encoding="utf-8") as f: spec = f.read(); break
            bundle, meta = design.propose(proj.home, spec or "", proj=proj, template=tmpl)
            path = design.write_project_pipeline(proj, bundle, template_meta=meta)
            print(json.dumps({"path": path, "meta": meta}))
            return 0
        from . import schedule, runtime as R
        if cmd == "step":
            ev = schedule.step(proj)
            out = {k: v for k, v in ev.items() if k != "run"}; out["run"] = ev.get("run") or R.load_run(proj)
            print(json.dumps(out, default=str)); return 0
        max_steps = 100
        if "--max" in args:
            i = args.index("--max"); max_steps = int(args[i + 1])
        run, events = schedule.run_until_idle(proj, max_steps=max_steps)
        print(json.dumps({"status": (run or {}).get("status"), "steps": (run or {}).get("step_count"),
                          "error": (run or {}).get("error"), "events": events[-30:]}, default=str))
        return 0 if (run or {}).get("status") == "COMPLETED" else 1
    print(f"unknown command {cmd}", file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())
