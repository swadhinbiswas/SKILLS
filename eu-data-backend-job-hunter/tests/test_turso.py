import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import turso  # noqa: E402


class TursoReadinessTests(unittest.TestCase):
    def test_upsert_column_order_matches_normalized_row_shape(self):
        self.assertLess(
            turso.UPSERT_COLS.index("eligible_countries"),
            turso.UPSERT_COLS.index("remote"),
        )

    @patch("turso.execute")
    @patch("turso.query_rows")
    @patch("turso.pipeline")
    def test_init_schema_migrates_old_table_before_indexes(self, pipeline, query_rows, execute):
        pipeline.return_value = {"results": [{"type": "ok"}]}
        query_rows.return_value = [
            {"name": "id"}, {"name": "title"}, {"name": "company"},
            {"name": "location"}, {"name": "country"}, {"name": "remote"},
            {"name": "url"}, {"name": "source"}, {"name": "posted"},
            {"name": "salary"}, {"name": "snippet"}, {"name": "description"},
            {"name": "role_fit"}, {"name": "seniority"}, {"name": "relocation"},
            {"name": "track"}, {"name": "company_url"},
        ]
        execute.return_value = {"type": "ok"}
        turso.init_schema("libsql://example", "token")
        alter_sql = [call.args[2] for call in execute.call_args_list]
        self.assertIn("ALTER TABLE jobs ADD COLUMN active INTEGER NOT NULL DEFAULT 1", alter_sql)
        self.assertIn("ALTER TABLE jobs ADD COLUMN first_seen TEXT", alter_sql)
        self.assertIn("ALTER TABLE jobs ADD COLUMN eligible_countries TEXT", alter_sql)

    @patch("turso.execute")
    @patch("turso.query_rows")
    def test_pruning_respects_recent_seen_grace_period(self, query_rows, execute):
        query_rows.return_value = [
            {"id": "recent", "last_seen": "2099-01-01T00:00:00Z"},
            {"id": "old", "last_seen": "2020-01-01T00:00:00Z"},
        ]
        execute.return_value = {"type": "ok", "response": {"result": {"rows_affected": 1}}}
        turso.deactivate_missing_jobs("libsql://example", "token", ["current"], grace_days=7)
        args = execute.call_args.args[3]
        self.assertIn("old", args)
        self.assertNotIn("recent", args)

    @patch("turso.pipeline")
    def test_upsert_rejects_a_partial_error_batch(self, pipeline):
        pipeline.return_value = {"results": [{"type": "error", "error": {"message": "bad row"}}]}
        with self.assertRaises(RuntimeError):
            turso.upsert_jobs("libsql://example", "token", [{
                "id": "1", "title": "Data Engineer", "company": "Example",
                "location": "Berlin", "country": "de", "remote": False,
                "url": "https://example.test/1", "source": "Fixture",
                "posted": "2026-09-25", "salary": None, "snippet": "",
                "description": "", "role_fit": "core", "seniority": "mid_plus",
                "relocation": False, "_track": "data_engineer",
            }])


class ApplicationTrackingTests(unittest.TestCase):
    def test_statuses_cover_the_pipeline(self):
        for status in ("draft", "ready", "applied", "interview", "offer", "rejected", "withdrawn"):
            self.assertIn(status, turso.APPLICATION_STATUSES)

    @patch("turso.execute")
    def test_upsert_application_uses_job_id_conflict(self, execute):
        execute.return_value = {"type": "ok"}
        turso.upsert_application("libsql://example", "token", {
            "job_id": "abc", "status": "draft", "subject": "Application: X",
        })
        sql, args = execute.call_args.args[2], execute.call_args.args[3]
        self.assertIn("INSERT INTO applications", sql)
        self.assertIn("ON CONFLICT(job_id)", sql)
        self.assertIn("abc", args)
        self.assertIn("draft", args)

    @patch("turso.execute")
    def test_update_status_rejects_unknown_value(self, execute):
        with self.assertRaises(ValueError):
            turso.update_application_status("libsql://example", "token", "abc", "ghosted")
        execute.assert_not_called()

    @patch("turso.execute")
    def test_update_status_applied_stamps_applied_at(self, execute):
        execute.return_value = {"type": "ok"}
        turso.update_application_status("libsql://example", "token", "abc", "applied")
        sql = execute.call_args.args[2]
        self.assertIn("applied_at", sql)
        self.assertIn("status = ?", sql)


if __name__ == "__main__":
    unittest.main()
