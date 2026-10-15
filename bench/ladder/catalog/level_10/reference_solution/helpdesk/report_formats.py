"""Rendering of the summary report in different formats."""
import json

FORMATS = ("text", "markdown", "json")

def _sections(report):
    out = [("Status", report["by_status"]), ("Priority", report["by_priority"]), ("Category", report["by_category"])]
    out += [(name, report[key]) for name, key in (("Severity", "by_severity"), ("Label", "by_label")) if report.get(key)]
    return out

def render_summary(report, fmt="text"):
    if fmt == "json": return json.dumps(report, indent=1, sort_keys=True)
    if fmt == "markdown":
        out = ["# Helpdesk summary", "", f"- Total tickets: {report['total']}", f"- Open: {report['open_count']}", f"- Average open age (hours): {report['avg_open_age_hours']}", ""]
        for name, counts in _sections(report):
            out += [f"## {name}", ""] + [f"- {k}: {v}" for k, v in sorted(counts.items())] + [""]
        return "\n".join(out).rstrip() + "\n"
    out = [f"Total: {report['total']}", f"Open: {report['open_count']}", f"Average open age (hours): {report['avg_open_age_hours']}"]
    for name, counts in _sections(report):
        for k, v in sorted(counts.items()): out.append(f"{name} {k}: {v}")
    return "\n".join(out)
