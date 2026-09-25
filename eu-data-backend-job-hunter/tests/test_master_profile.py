import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import master_profile as prof  # noqa: E402


def sample_profile() -> dict:
    return {
        "tracks": ["data_engineer", "backend_engineer"],
        "seniority_target": ["junior", "mid_plus"],
        "preferred_countries": ["de", "nl"],
        "remote_preference": "hybrid",
        "willing_to_relocate": True,
        "work_authorization": ["eu"],
        "salary": {"currency": "EUR", "min": 55000, "target": 70000},
        "skills": {
            "core": ["python", "sql", "postgresql", "airflow", "dbt"],
            "familiar": ["docker", "kubernetes", "aws"],
            "learning": ["rust"],
        },
        "excluded_companies": ["Spam Corp"],
        "excluded_keywords": ["unpaid"],
    }


class ProfileTests(unittest.TestCase):
    def test_normalize_skill_aliases(self):
        self.assertEqual(prof.normalize_skill("Postgres"), "postgresql")
        self.assertEqual(prof.normalize_skill("Golang"), "go")
        self.assertEqual(prof.normalize_skill("k8s"), "kubernetes")
        self.assertEqual(prof.normalize_skill("PowerBI"), "power bi")

    def test_profile_roundtrip_json_and_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps(sample_profile()))
            loaded = prof.load_profile(path)
            self.assertIn("python", loaded["skills"]["core"])
            self.assertTrue(prof.profile_is_usable(loaded))

    def test_missing_profile_is_empty_not_fatal(self):
        loaded = prof.load_profile("/nonexistent/does/not/exist.json")
        self.assertEqual(loaded, {})
        self.assertFalse(prof.profile_is_usable(loaded))

    def test_gap_detection_finds_missing_skills(self):
        profile = prof._normalize_profile(sample_profile())
        gaps = prof.detect_gaps("We need Rust and Scala and Snowflake and Kafka", profile)
        self.assertIn("scala", gaps)
        self.assertIn("snowflake", gaps)
        self.assertIn("kafka", gaps)
        self.assertNotIn("rust", gaps)  # listed under learning → not a gap
        self.assertNotIn("python", gaps)  # already a core skill

    def test_strong_match_scores_high_and_lists_reasons(self):
        profile = prof._normalize_profile(sample_profile())
        job = {
            "title": "Senior Data Engineer",
            "company": "Good GmbH",
            "location": "Berlin, Germany",
            "country": "de",
            "remote": False,
            "track": "data_engineer",
            "seniority": "mid_plus",
            "posted": "2026-09-25",
            "salary": "€80,000-95,000/year",
            "description": "Build Airflow and dbt pipelines in Python with PostgreSQL and Docker on Kubernetes.",
        }
        result = prof.score_job(job, profile)
        self.assertGreaterEqual(result["match_score"], 75)
        self.assertFalse(result["excluded"])
        self.assertTrue(any("core skills" in r for r in result["reasons"]))
        self.assertGreaterEqual(len(result["matched_skills"]), 4)

    def test_frontend_job_scores_low(self):
        profile = prof._normalize_profile(sample_profile())
        job = {
            "title": "Frontend Engineer",
            "company": "Site Ltd",
            "location": "Paris, France",
            "country": "fr",
            "remote": False,
            "track": "software_engineer",
            "seniority": "mid_plus",
            "description": "React CSS UI design systems.",
        }
        result = prof.score_job(job, profile)
        self.assertLess(result["match_score"], 40)
        self.assertTrue(result["gaps"])

    def test_excluded_company_is_vetoed(self):
        profile = prof._normalize_profile(sample_profile())
        job = {
            "title": "Data Engineer", "company": "Spam Corp", "country": "de",
            "track": "data_engineer", "seniority": "mid_plus", "description": "python sql",
        }
        result = prof.score_job(job, profile)
        self.assertTrue(result["excluded"])
        self.assertEqual(result["match_score"], 0)

    def test_excluded_keyword_is_vetoed(self):
        profile = prof._normalize_profile(sample_profile())
        job = {"title": "Unpaid Data Engineer", "company": "X", "description": "python"}
        self.assertTrue(prof.score_job(job, profile)["excluded"])

    def test_salary_parser_handles_common_formats(self):
        self.assertEqual(prof._parse_salary("€70,000-85,000/year"), 70000)
        self.assertEqual(prof._parse_salary("70.000 - 85.000 EUR"), 70000)
        self.assertEqual(prof._parse_salary("£90k"), 90000)

    def test_score_items_degrades_without_profile(self):
        items = [{"title": "Data Engineer", "company": "X", "description": "python"}]
        prof.score_items(items, {})
        self.assertNotIn("match_score", items[0])

    def test_score_items_annotates(self):
        profile = prof._normalize_profile(sample_profile())
        items = [{
            "title": "Data Engineer", "company": "X", "country": "de",
            "track": "data_engineer", "seniority": "mid_plus",
            "description": "python sql airflow",
        }]
        prof.score_items(items, profile)
        self.assertIn("match_score", items[0])
        self.assertIsInstance(items[0]["match_score"], int)


if __name__ == "__main__":
    unittest.main()
