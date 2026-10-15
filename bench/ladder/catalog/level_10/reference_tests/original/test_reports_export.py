import csv, json
from base import Base
from helpdesk import export, reports

class TestReports(Base):
    def setUp(self):
        super().setUp(); self.a = self.ticket(priority="urgent"); self.b = self.ticket("B", priority="low", category="billing"); self.hd.tickets.assign(self.a.id, self.agent.id, self.admin.id); self.hd.tickets.set_status(self.b.id, "closed", self.agent.id)
    def test_summary_counts(self):
        r = reports.summary(self.hd.store, "2024-03-01T19:00:00"); self.assertEqual((r["total"], r["open_count"], r["by_status"], r["by_category"]), (2, 1, {"in_progress": 1, "closed": 1}, {"general": 1, "billing": 1})); self.assertEqual(r["avg_open_age_hours"], 10.0)
    def test_sla_breaches_most_overdue_first(self): c = self.ticket("C", priority="high"); late = reports.sla_breaches(self.hd.store, "2024-03-02T09:00:00"); self.assertEqual([t.id for t in late], [self.a.id, c.id])
    def test_workload(self): self.assertEqual(reports.workload(self.hd.store), {self.agent.id: 1})
    def test_resolution_times(self): self.clock.ts = "2024-03-01T12:00:00"; self.hd.tickets.set_status(self.a.id, "closed", self.agent.id); self.assertEqual(reports.resolution_times(self.hd.store)[self.a.id], 3.0)

class TestExport(Base):
    def setUp(self): super().setUp(); self.a = self.ticket("Comma, in title", priority="high"); self.hd.tickets.assign(self.a.id, self.agent.id, self.admin.id)
    def test_csv_has_the_documented_leading_columns(self):
        p = self.dir + "/t.csv"; self.assertEqual(export.export_csv(self.hd.store, p), 1)
        rows = list(csv.reader(open(p))); self.assertEqual(rows[0][:10], export.CSV_COLUMNS[:10]); self.assertEqual(rows[1][:10], ["1", "Comma, in title", "in_progress", "high", "general", "Cy", "Bob", "2024-03-01T09:00:00", "2024-03-01T09:00:00", "2024-03-01T17:00:00"])
    def test_json_contains_the_ticket_fields(self):
        p = self.dir + "/t.json"; export.export_json(self.hd.store, p); t = json.load(open(p))["tickets"][0]
        for k in ("id", "title", "description", "requester_id", "assignee_id", "status", "priority", "category", "created", "updated", "sla_due", "comments"): self.assertIn(k, t)
        self.assertEqual((t["priority"], t["assignee_id"]), ("high", self.agent.id))
    def test_export_is_atomic(self): export.export_csv(self.hd.store, self.dir + "/t.csv"); self.assertFalse([f for f in __import__("os").listdir(self.dir) if f.startswith(".export")])
