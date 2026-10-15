"""Benchmark notifications through the normal ntfy path (`codebot notify`, which honours NTFY_ENABLED and writes the egress log). Four kinds only:
complete (one per finished run), timeout (rate limited and batched), critical (infrastructure trouble the runner cannot fix), human (interactive mode needs an answer).
Routine progress never notifies."""
import os, subprocess, time

class Notifier:
    def __init__(self, home, enabled=True, timeout_gap_s=1800, sender=None):
        self.home, self.enabled, self.gap = home, enabled, timeout_gap_s; self.last_timeout = 0.0; self.pending_timeouts = []; self.sent = []; self._sender = sender
    def _send(self, title, msg, prio, tags):
        self.sent.append((title, msg, prio, tags))
        if not self.enabled: return
        if self._sender: return self._sender(title, msg, prio, tags)
        try: subprocess.run([os.path.join(self.home, "bin", "codebot"), "notify", title, msg, prio, tags], capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError): pass
    def complete(self, run_id, summary): self._send(f"codebot bench: {run_id} finished", summary, "high", "white_check_mark")
    def critical(self, run_id, msg): self._send(f"codebot bench: {run_id} needs attention", msg, "urgent", "warning")
    def human(self, run_id, trial, task, question): self._send(f"codebot bench: {trial} needs you ({task})", question[:400], "urgent", "sos")
    def timeout(self, run_id, trial, task, used_min, limit_min, now=time.time):
        """Batched: at most one message per `gap` seconds, listing every task that timed out since the last one."""
        self.pending_timeouts.append(f"{trial} {task} ({used_min:.0f}/{limit_min:.0f} min)")
        if now() - self.last_timeout >= self.gap: self.flush_timeouts(run_id, now)
    def flush_timeouts(self, run_id, now=time.time):
        if not self.pending_timeouts: return
        self._send(f"codebot bench: {run_id} task timeouts", "; ".join(self.pending_timeouts[:12]) + (f" (+{len(self.pending_timeouts) - 12} more)" if len(self.pending_timeouts) > 12 else ""), "default", "hourglass_flowing_sand")
        self.pending_timeouts = []; self.last_timeout = now()
