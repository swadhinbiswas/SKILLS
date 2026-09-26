import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import import_resume  # noqa: E402
import master_profile  # noqa: E402


RESUME = {
    "basics": {
        "name": "Jane Doe",
        "label": "Data Engineer",
        "email": "jane@example.com",
        "phone": "+1 555",
        "summary": "Engineer.",
        "location": {"city": "Berlin", "region": "Germany", "countryCode": "DE"},
        "profiles": [
            {"network": "GitHub", "url": "https://github.com/jane"},
            {"network": "Hugging Face", "url": "https://huggingface.co/jane"},
        ],
    },
    "work": [{
        "name": "Acme", "position": "Data Engineer", "location": "Remote",
        "startDate": "2022-01", "endDate": "Present",
        "highlights": ["Built pipelines.", "Ran the warehouse."],
    }],
    "projects": [{
        "name": "Lakehouse", "description": "A lakehouse.",
        "links": {"source": "https://github.com/jane/lakehouse"},
        "highlights": ["Streamed 1TB/day."],
    }],
    "skills": [
        {"name": "Languages", "level": "core", "keywords": ["Python", "SQL"]},
        {"name": "Tools", "level": "working", "keywords": ["dbt", "Kafka"]},
        {"name": "Learning", "level": "learning", "keywords": ["Polars"]},
    ],
    "languagesSpoken": [{"language": "English", "fluency": "Fluent", "cefr": "C2"}],
    "education": [{
        "institution": "Some University", "studyType": "B.Sc.", "area": "CS",
        "startDate": "2018", "endDate": "2022", "courses": ["Databases", "Algorithms"],
    }],
    "certificates": [{"name": "AWS", "issuer": "Amazon", "date": "2024-05"}],
    "publications": [{"name": "A Paper", "publisher": "arXiv", "releaseDate": "2025-01", "url": "https://arxiv.org/x"}],
    "awards": [{"title": "Best thing", "date": "2025", "awarder": "Acme"}],
    "jobSearch": {
        "tracks": ["data_engineer"],
        "seniorityTarget": ["mid_plus"],
        "preferredCountries": ["de", "nl"],
        "remotePreference": "any",
        "willingToRelocate": True,
        "workAuthorization": ["requires_sponsorship"],
        "salary": {"currency": "EUR", "min": 50000, "target": 70000},
        "excludedKeywords": ["unpaid"],
        "customNote": "Blue Card eligible.",
    },
}


class ImportResumeTests(unittest.TestCase):
    def setUp(self):
        self.profile = import_resume.convert(RESUME)

    def test_basics_mapped(self):
        self.assertEqual(self.profile["name"], "Jane Doe")
        self.assertEqual(self.profile["headline"], "Data Engineer")
        self.assertEqual(self.profile["email"], "jane@example.com")
        self.assertEqual(self.profile["location"], "Berlin, Germany, DE")
        self.assertIn("hugging_face", self.profile["links"])

    def test_skills_split_by_level(self):
        self.assertEqual(self.profile["skills"]["core"], ["Python", "SQL"])
        self.assertEqual(self.profile["skills"]["familiar"], ["dbt", "Kafka"])
        self.assertEqual(self.profile["skills"]["learning"], ["Polars"])

    def test_work_project_education(self):
        self.assertEqual(self.profile["experience"][0]["company"], "Acme")
        self.assertEqual(self.profile["experience"][0]["end"], "present")
        self.assertEqual(self.profile["experience"][0]["bullets"][0], "Built pipelines.")
        self.assertEqual(self.profile["projects"][0]["link"], "https://github.com/jane/lakehouse")
        self.assertEqual(self.profile["education"][0]["degree"], "B.Sc. CS")
        self.assertEqual(self.profile["education"][0]["details"], "Databases, Algorithms")

    def test_job_search_preferences_and_extras(self):
        self.assertEqual(self.profile["tracks"], ["data_engineer"])
        self.assertEqual(self.profile["preferred_countries"], ["de", "nl"])
        self.assertEqual(self.profile["work_authorization"], ["requires_sponsorship"])
        self.assertEqual(self.profile["salary"]["target"], 70000)
        self.assertEqual(self.profile["excluded_keywords"], ["unpaid"])
        self.assertIn("Blue Card", self.profile["application"]["custom_note"])
        self.assertEqual(len(self.profile["publications"]), 1)
        self.assertEqual(len(self.profile["awards"]), 1)

    def test_generated_profile_is_loadable_and_usable(self):
        # Round-trip: the converted dict must satisfy the scorer's normalizer.
        normalized = master_profile._normalize_profile(self.profile)
        self.assertTrue(master_profile.profile_is_usable(normalized))
        self.assertIn("python", normalized["skills"]["core"])


if __name__ == "__main__":
    unittest.main()
