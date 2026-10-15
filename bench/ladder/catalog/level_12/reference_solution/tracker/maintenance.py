import threading, time
from .core import TRANSITIONS

def _days_ago(ts, days):
    import datetime; return (datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S") - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

class Maintenance:
    """Periodic housekeeping: close issues that stayed resolved, delete expired tokens, check the integrity of the data."""
    def __init__(self, tracker, close_after_days=14):
        self.tracker, self.close_after_days = tracker, close_after_days
        self.last_report, self.last_error, self.runs, self._stop, self._thread = None, None, 0, threading.Event(), None

    def run_once(self):
        t = self.tracker; report = {"closed": [], "tokens_purged": 0, "problems": []}
        def go():
            now = t.clock(); limit = _days_ago(now, self.close_after_days)
            for iid, project, number in t.db.execute("SELECT id, project_key, number FROM issues WHERE status = 'resolved' AND updated < ? ORDER BY id", (limit,)).fetchall():
                t.db.execute("UPDATE issues SET status = 'closed', closed = ?, updated = ? WHERE id = ?", (now, now, iid)); key = f"{project}-{number}"
                t._audit("system", "issue.transition", "issue", key, {"status": ["resolved", "closed"]}); report["closed"].append(key)
            n = t.db.execute("DELETE FROM tokens WHERE expires <= ?", (now,)).rowcount
            if n: t._audit("system", "maintenance.purge_tokens", "token", None, {"deleted": [0, n]})
            report["tokens_purged"] = n
        t.tx(go); report["problems"] = self.check_integrity(); self.last_report = report; self.runs += 1; return report

    def check_integrity(self):
        t = self.tracker; problems = []
        with t._lock:
            for key, nxt in t.db.execute("SELECT key, next_number FROM projects"):
                top = t.db.execute("SELECT COALESCE(MAX(number), 0) FROM issues WHERE project_key = ?", (key,)).fetchone()[0]
                if nxt <= top: problems.append(f"project {key}: next_number {nxt} is not above the highest issue number {top}")
            for (n,) in t.db.execute("SELECT COUNT(*) FROM issues i WHERE status NOT IN ('open','in_progress','resolved','closed')"):
                if n: problems.append(f"{n} issue(s) have an unknown status")
            for key, number in t.db.execute("SELECT project_key, number FROM issues WHERE (status = 'closed') != (closed IS NOT NULL)"): problems.append(f"issue {key}-{number}: closed time and status disagree")
            seqs = [r[0] for r in t.db.execute("SELECT seq FROM audit ORDER BY seq")]
            if seqs != list(range(1, len(seqs) + 1)): problems.append("the audit sequence has gaps")
        return problems

    def start(self, interval=60.0):
        if self._thread and self._thread.is_alive(): return self
        self._stop.clear()
        def loop():
            while not self._stop.is_set():
                try: self.run_once()
                except Exception as e: self.last_error = f"{type(e).__name__}: {e}"
                self._stop.wait(interval)
        self._thread = threading.Thread(target=loop, daemon=True); self._thread.start(); return self

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(30); self._thread = None
