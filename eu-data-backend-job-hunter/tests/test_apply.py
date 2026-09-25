import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import apply as applier  # noqa: E402
import master_profile  # noqa: E402


APP_PROFILE = {
    "name": "Alex Doe",
    "headline": "Data Engineer",
    "email": "alex@example.com",
    "phone": "+49 30 123",
    "location": "Berlin, Germany",
    "links": {"github": "https://github.com/alex"},
    "skills": {
        "core": ["python", "sql", "airflow", "dbt"],
        "familiar": ["docker", "terraform"],
        "learning": ["rust"],
    },
    "experience": [
        {
            "company": "Example GmbH", "title": "Data Engineer",
            "location": "Berlin", "start": "2023-01", "end": "present",
            "bullets": [
                "Set up CI/CD with GitHub Actions.",
                "Built Airflow and dbt pipelines in Python on PostgreSQL.",
                "Wrote internal documentation.",
            ],
        }
    ],
    "application": {"sign_off": "Kind regards", "custom_note": "EU work authorised."},
}

JOB = {
    "id": "job-1",
    "title": "Senior Data Engineer",
    "company": "Nordwind GmbH",
    "location": "Berlin, Germany",
    "url": "https://example.test/jobs/1",
    "description": "Python, SQL, Airflow, dbt, PostgreSQL and Terraform. Contact jobs@nordwind.test.",
    "match_score": 80,
    "matched_skills": "python,sql,airflow,dbt",
    "missing_skills": "snowflake",
}


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.profile = master_profile._normalize_profile(APP_PROFILE)

    def test_bullets_reordered_by_job_relevance(self):
        ctx = applier.build_context(JOB, self.profile)
        bullets = ctx["experience"][0]["bullets"]
        self.assertIn("Airflow", bullets[0])

    def test_skills_reordered_relevant_first(self):
        ctx = applier.build_context(JOB, self.profile)
        self.assertEqual(ctx["skills"][0], "Airflow")

    def test_cover_letter_is_well_formed(self):
        ctx = applier.build_context(JOB, self.profile)
        letter = applier.render_cover_letter(ctx)
        self.assertTrue(letter.startswith("# Application"))
        self.assertIn("**Candidate:** Alex Doe", letter)
        # The earlier Jinja bug glued header lines together; guard against it.
        self.assertIn("\n**Contact:**", letter)
        self.assertIn("Nordwind GmbH", letter)
        self.assertIn("Kind regards", letter)

    def test_email_has_recipient_subject_and_match(self):
        ctx = applier.build_context(JOB, self.profile)
        email = applier.render_email(ctx)
        self.assertTrue(email.startswith("To: jobs@nordwind.test"))
        self.assertIn("Subject: Application: Senior Data Engineer", email)
        self.assertIn("Match score: 80/100", email)
        self.assertIn("\nMy CV is attached.", email)

    def test_extract_email_ignores_noreply(self):
        self.assertIsNone(applier._extract_email("write to noreply@x.com"))
        self.assertEqual(applier._extract_email("mail me at hi@corp.io"), "hi@corp.io")

    def test_tailor_job_writes_pack_without_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = applier.tailor_job(JOB, self.profile, out_root=Path(tmp), make_pdf=False)
            out = Path(tmp) / "job-1"
            for name in ("resume.md", "resume.html", "cover_letter.md", "email.txt", "job.json", "meta.json"):
                self.assertTrue((out / name).exists(), name)
            self.assertNotIn("resume_pdf", manifest["files"])
            self.assertEqual(manifest["recipient"], "jobs@nordwind.test")

    def test_tailor_requires_a_profile(self):
        with self.assertRaises(RuntimeError):
            applier.tailor_job(JOB, {}, make_pdf=False)

    @patch.object(applier.turso, "upsert_application")
    @patch.object(applier.turso, "get_application")
    def test_record_draft_preserves_existing_status(self, get_app, upsert):
        get_app.return_value = {
            "status": "applied",
            "applied_at": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
        }
        manifest = {
            "job_id": "j", "match_score": 70, "recipient": "a@b.co",
            "subject": "S", "files": {"resume_html": "/x/resume.html"},
        }
        applier.record_draft("libsql://x", "t", manifest)
        passed = upsert.call_args.args[2]
        self.assertEqual(passed["status"], "applied")
        self.assertEqual(passed["applied_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(passed["created_at"], "2026-01-01T00:00:00Z")

    @patch.object(applier.turso, "upsert_application")
    @patch.object(applier.turso, "get_application")
    def test_record_draft_defaults_to_draft_for_new_job(self, get_app, upsert):
        get_app.return_value = None
        manifest = {"job_id": "new", "match_score": 55, "recipient": "", "subject": "S", "files": {}}
        applier.record_draft("libsql://x", "t", manifest)
        self.assertEqual(upsert.call_args.args[2]["status"], "draft")


if __name__ == "__main__":
    unittest.main()
