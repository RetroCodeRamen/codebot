"""In-subprocess runner for ONE reference test file. Usage: refrun.py <tests_dir> <workspace> <test_file.py> <per_test_timeout_s>
Prints one JSON line per test as it finishes: {"id","status","msg","secs"}; status = pass | fail | error | skip | timeout.
Each test runs under SIGALRM so a hung test is reported (and the rest still run); the parent adds a hard process timeout on top."""
import importlib.util, json, os, signal, sys, time, traceback, unittest

class _Timeout(BaseException):
    pass

def _alarm(sig, frm): raise _Timeout()

def _flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite): yield from _flat(t)
        else: yield t

def main(argv):
    tests_dir, ws, fn, per = os.path.realpath(argv[1]), os.path.realpath(argv[2]), argv[3], int(argv[4])
    os.chdir(ws); sys.path[:0] = [ws, tests_dir]; signal.signal(signal.SIGALRM, _alarm)
    mod_name = fn[:-3]
    try:
        spec = importlib.util.spec_from_file_location(mod_name, os.path.join(tests_dir, fn)); mod = importlib.util.module_from_spec(spec); sys.modules[mod_name] = mod; spec.loader.exec_module(mod)
        suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
    except BaseException as e:           # the test file itself could not load: usually the workspace lacks a module the tests import at top level
        print(json.dumps({"id": f"{mod_name}.<import>", "status": "error", "msg": f"{type(e).__name__}: {str(e)[:300]}", "secs": 0}), flush=True); return 0
    for t in _flat(suite):
        tid = t.id(); t0 = time.time(); status, msg = "pass", ""
        res = unittest.TestResult()
        signal.alarm(per)
        try: t.run(res)
        except _Timeout: status, msg = "timeout", f"exceeded {per} s"
        finally: signal.alarm(0)
        if status == "pass":
            if res.errors and "_Timeout" in res.errors[0][1]: status, msg = "timeout", f"exceeded {per} s"
            elif res.errors: status, msg = "error", res.errors[0][1].strip().splitlines()[-1][:300]
            elif res.failures: status, msg = "fail", res.failures[0][1].strip().splitlines()[-1][:300]
            elif res.skipped: status, msg = "skip", str(res.skipped[0][1])[:200]
        print(json.dumps({"id": tid, "status": status, "msg": msg, "secs": round(time.time() - t0, 2)}), flush=True)
    return 0

if __name__ == "__main__": sys.exit(main(sys.argv))
