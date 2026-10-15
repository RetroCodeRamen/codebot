"""The background worker: fires due reminders and purges expired tokens."""
import threading

class Worker:
    """Runs the periodic jobs of the application: firing due reminders and deleting expired tokens."""
    def __init__(self, store, interval=1.0, clock=None):
        self.store, self.interval, self.clock = store, interval, clock
        self.runs, self.fired_total, self.last_report, self.last_error, self._stop, self._thread = 0, 0, None, None, threading.Event(), None

    def run_once(self, now=None):
        now = now or (self.clock() if self.clock else None); report = {"fired": self.store.fire_due(now), "tokens_purged": self.store.purge_tokens(now)}
        self.runs += 1; self.fired_total += len(report["fired"]); self.last_report = report; return report

    def start(self):
        if self._thread and self._thread.is_alive(): return self
        self._stop.clear()
        def loop():
            while not self._stop.is_set():
                try: self.run_once()
                except Exception as e: self.last_error = f"{type(e).__name__}: {e}"
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=loop, daemon=True); self._thread.start(); return self

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(30); self._thread = None
