"""Append-only, hash-chained audit log (F10). Every command, file touch, model request and check run is recorded.
Tamper-EVIDENT (not root-append-only): each entry hashes the previous one, so edits and mid-file deletions break the
chain; dropping the tail is caught only while audit.head is intact. The harness writes it; the sandboxed worker cannot."""
import fcntl, hashlib, json, os, time

def _path(proj): return proj.p("audit.jsonl")
def _head(proj): return proj.p("audit.head")

def _canon(e): return json.dumps(e, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
def _h(prev, e): return hashlib.sha256((prev + _canon(e)).encode()).hexdigest()

def _last(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 8192)); lines = f.read().decode(errors="replace").splitlines()
        for ln in reversed(lines):
            try: r = json.loads(ln); return r["seq"], r["hash"]
            except (ValueError, KeyError): continue
    except OSError: pass
    return 0, "0" * 64

def log(proj, kind, **data):
    """Append one entry. Never raises: auditing must not take the run down (a failure is reported by verify())."""
    try:
        with open(_path(proj) + ".lock", "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            seq, prev = _last(_path(proj))
            e = {"seq": seq + 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **data}
            e["hash"] = _h(prev, e)
            with open(_path(proj), "a") as f: f.write(json.dumps(e, ensure_ascii=False) + "\n")
            with open(_head(proj), "w") as f: f.write(f"{e['seq']} {e['hash']}\n")
            fcntl.flock(lf, fcntl.LOCK_UN)
    except OSError: pass

def entries(proj):
    out = []
    try:
        with open(_path(proj)) as f:
            for ln in f:
                try: out.append(json.loads(ln))
                except ValueError: out.append({"seq": None, "bad": ln[:80]})
    except OSError: pass
    return out

def verify(proj):
    """(ok, count, problem). Walks the chain from the start."""
    prev, n, seq = "0" * 64, 0, 0
    for e in entries(proj):
        if e.get("seq") is None: return False, n, f"unreadable line after entry {n}"
        claimed = e.pop("hash", None)
        if e["seq"] != seq + 1: return False, n, f"sequence gap at entry {e['seq']} (expected {seq + 1})"
        if _h(prev, e) != claimed: return False, n, f"hash mismatch at entry {e['seq']} (edited or inserted)"
        prev, seq, n = claimed, e["seq"], n + 1
    try:
        with open(_head(proj)) as f: hs, hh = f.read().split()
        if (int(hs), hh) != (seq, prev): return False, n, f"log ends at entry {seq} but audit.head says {hs}: the tail was cut"
    except (OSError, ValueError):
        if n: return False, n, "audit.head is missing"
    return True, n, ""

def egress(proj_home):
    """Harness-side external traffic recorded by the bash supervisor: [{ts, project, dest, purpose}]."""
    out = []
    try:
        with open(os.path.join(proj_home, "logs", "egress.jsonl")) as f:
            for ln in f:
                try: out.append(json.loads(ln))
                except ValueError: pass
    except OSError: pass
    return out

def report(proj, probe=None):
    ok, n, problem = verify(proj); es = entries(proj) if ok else [e for e in entries(proj) if e.get("seq") is not None]
    cmds = [e for e in es if e["kind"] == "command"]; checks = [e for e in es if e["kind"] == "check_run"]
    writes = [e for e in es if e["kind"] == "file_write"]; reqs = [e for e in es if e["kind"] == "model_request"]
    denied = [e for e in es if e["kind"] == "command_denied"]
    sandboxed = [e for e in cmds + checks if str(e.get("sandbox", "")).startswith("docker")]
    unsandboxed = [e for e in cmds + checks if e not in sandboxed]
    files = {}
    for w in writes: files[w["path"]] = w.get("sha_after")
    hosts = sorted({e.get("endpoint") for e in reqs})
    eg = [e for e in egress(proj.home) if e.get("project") in (proj.name, None, "")]
    eg_dest = sorted({e.get("dest") for e in eg})
    warnings = []
    if not ok: warnings.append(f"AUDIT CHAIN BROKEN: {problem}")
    if unsandboxed: warnings.append(f"{len(unsandboxed)} command/check run(s) were NOT sandboxed (no network isolation)")
    if any(h and not str(h).startswith("127.0.0.1:") for h in hosts): warnings.append(f"model traffic left loopback: {hosts}")
    if any(d not in ("ntfy.sh",) and d != proj.get("NTFY_HOST", "ntfy.sh") for d in eg_dest if d): warnings.append(f"unexpected harness egress: {eg_dest}")
    if probe is not None and not probe.get("blocked"): warnings.append(f"NETWORK PROBE REACHED THE INTERNET: {probe}")
    return {"project": proj.name, "chain_ok": ok, "entries": n, "problem": problem,
            "commands": len(cmds), "sandboxed": len(sandboxed), "unsandboxed": len(unsandboxed), "denied": len(denied),
            "checks": len(checks), "files_written": len(files), "files": files,
            "model_requests": len(reqs), "model_endpoints": hosts, "tokens": sum((e.get("prompt_tokens", 0) + e.get("gen_tokens", 0)) for e in reqs),
            "egress": len(eg), "egress_dest": eg_dest, "probe": probe, "warnings": warnings,
            "top_commands": [e.get("command", "")[:90] for e in cmds[-8:]], "sandbox_modes": sorted({str(e.get("sandbox")) for e in cmds + checks})}

def render(r):
    L = [f"audit: {r['project']}"]
    L.append(f"chain:     {'OK' if r['chain_ok'] else 'BROKEN'} ({r['entries']} entries)" + (f" - {r['problem']}" if r["problem"] else ""))
    L.append(f"sandbox:   {r['sandboxed']} sandboxed, {r['unsandboxed']} unsandboxed of {r['commands'] + r['checks']} commands/checks  [{', '.join(r['sandbox_modes']) or 'none run'}]")
    L.append(f"commands:  {r['commands']} run, {r['denied']} refused by policy; {r['checks']} acceptance checks")
    L.append(f"files:     {r['files_written']} written/edited by the worker" + ("".join(f"\n             {p}" for p in list(r["files"])[:15]) if r["files"] else ""))
    L.append(f"model:     {r['model_requests']} requests, {r['tokens']} tokens, endpoints: {', '.join(r['model_endpoints']) or 'none'}")
    L.append(f"egress:    {'harness notifications to ' + ', '.join(r['egress_dest']) + f' ({r['egress']} sent)' if r['egress'] else 'none recorded'}")
    if r["probe"] is not None: L.append(f"probe:     {'network BLOCKED inside the sandbox' if r['probe'].get('blocked') else 'NETWORK REACHABLE'} ({r['probe'].get('detail', '')})")
    L.append("verdict:   " + ("OFFLINE-CLEAN: worker traffic is loopback-only, commands had no network" if not r["warnings"] and r["sandboxed"] else
                              "WARNINGS" if r["warnings"] else "nothing to attest (no sandboxed commands were run)"))
    L += [f"  ! {w}" for w in r["warnings"]]
    return "\n".join(L)
