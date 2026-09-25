import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import job_search  # noqa: E402


class JobSearchReadinessTests(unittest.TestCase):
    def test_track_uses_title_before_search_context(self):
        self.assertEqual(
            job_search.classify_track(
                "Senior Backend Engineer - Python & Rust (ML / AI)",
                "Build data platforms and pipelines",
            ),
            ("backend_engineer", "core"),
        )
        self.assertEqual(
            job_search.classify_track(
                "Senior Software Engineer",
                "Build backend APIs, event-driven services, and PostgreSQL systems",
            ),
            ("software_engineer", "core"),
        )

    def test_seniority_ignores_incidental_description_words_and_handles_ranges(self):
        self.assertEqual(
            job_search.classify_seniority("Data Engineer", "Work with our senior leadership team"),
            "unclear",
        )
        self.assertEqual(
            job_search.classify_seniority("Data Engineer", "0-2 years of experience"),
            "junior",
        )
        self.assertEqual(
            job_search.classify_seniority("Data Engineer", "3+ years of experience"),
            "mid_plus",
        )
        self.assertEqual(
            job_search.classify_seniority("Data Engineer", "No experience required"),
            "junior",
        )
        self.assertEqual(
            job_search.classify_seniority("Data Engineer", "We run an internship program"),
            "unclear",
        )

    def test_generic_software_is_not_core_without_backend_data_context(self):
        self.assertEqual(
            job_search.classify_track("Software Engineer", "Build React components and CSS"),
            None,
        )
        self.assertEqual(
            job_search.classify_track("Full Stack Engineer", "Build frontend React and UI screens"),
            None,
        )
        self.assertEqual(
            job_search.classify_track("Software Engineer", "Build services and APIs"),
            ("software_engineer", "core"),
        )
        self.assertIsNone(
            job_search.classify_track("Software Engineer", "Build Android and iOS mobile apps")
        )
        self.assertIsNone(
            job_search.classify_track("Site Reliability Engineer", "Docker, Linux, and cloud operations")
        )

    def test_non_engineering_data_mentions_are_rejected(self):
        self.assertIsNone(
            job_search.classify_track(
                "Community Manager Data Platform",
                "data platform engineer, pipelines, and analytics",
            )
        )
        self.assertIsNone(
            job_search.classify_track("Frontend Engineer", "React, CSS, and UX")
        )

    def test_internships_are_hard_excluded_even_with_senior_text(self):
        self.assertEqual(
            job_search.classify_seniority(
                "Internship - Data Engineer",
                "Work with a senior engineering team",
            ),
            "internship",
        )
        self.assertEqual(job_search.classify_seniority("Junior Data Engineer", ""), "junior")
        self.assertEqual(job_search.classify_seniority("Mid-level Data Engineer", ""), "mid_plus")
        self.assertEqual(
            job_search.classify_seniority("Junior/Mid Backend Engineer", ""),
            "mixed",
        )

    def test_shared_feeds_are_limited_to_requested_country_scope(self):
        items = [
            {"country": "", "location": "London, United Kingdom", "remote": False, "source": "Arbeitnow", "url": "https://www.arbeitnow.co.uk/job/1", "snippet": "backend"},
            {"country": "", "location": "Berlin, Germany", "remote": False, "source": "Arbeitnow", "url": "https://www.arbeitnow.com/job/2", "snippet": "data"},
            {"country": "", "location": "Austria / Germany", "remote": False, "source": "Fixture", "url": "https://example.test/3", "snippet": "backend"},
        ]
        kept = job_search.filter_scope(items, ["de"])
        self.assertEqual(len(kept), 2)
        self.assertIn("Germany", kept[1]["location"])
        self.assertEqual(kept[1]["country"], "")
        self.assertEqual(kept[1]["eligible_countries"], "at,de")

    def test_salary_keeps_source_period_instead_of_guessing(self):
        self.assertEqual(
            job_search._adzuna_salary({
                "salary_min": 70000,
                "salary_max": 85000,
                "salary_currency": "EUR",
                "salary_min_time_period": "YEAR",
            }),
            "€70,000-85,000/year",
        )

    def test_default_level_filter_keeps_junior_and_mid_plus_but_not_internships(self):
        items = [
            {"seniority": "junior"},
            {"seniority": "mid_plus"},
            {"seniority": "unclear"},
            {"seniority": "mixed"},
            {"seniority": "internship"},
        ]
        kept = job_search.filter_seniority(items)
        self.assertEqual(
            [item["seniority"] for item in kept],
            ["junior", "mid_plus", "unclear", "mixed"],
        )
        self.assertEqual(
            job_search.filter_seniority([{"seniority": "internship"}], "all"),
            [],
        )
        self.assertEqual(
            job_search.filter_seniority([{"seniority": "internship"}], {"all", "internship"}),
            [{"seniority": "internship"}],
        )

    def test_worldwide_remote_requires_eu_context(self):
        self.assertFalse(job_search._remote_eu_ok("Anywhere in the World", "Build backend APIs"))
        self.assertFalse(job_search._remote_eu_ok("Remote", "Build backend APIs"))
        self.assertFalse(job_search._remote_eu_ok("USA", "Our German office serves clients"))
        self.assertFalse(
            job_search.filter_scope([{
                "country": "", "location": "Remote", "remote": True,
                "source": "Fixture", "url": "https://example.test/remote-de",
                "description": "Our German office serves clients",
            }], ["de"])
        )
        self.assertTrue(
            job_search.filter_scope([{
                "country": "", "location": "Remote", "remote": True,
                "source": "Fixture", "url": "https://example.test/remote-lu",
                "description": "Eligible candidates in Luxembourg",
            }], ["lu"])
        )
        self.assertEqual(
            job_search.filter_scope([{
                "country": "", "location": "New York, USA", "remote": False,
                "source": "Fixture", "url": "https://example.test/us", "description": "German clients",
            }], ["de"]),
            [],
        )
        self.assertTrue(
            job_search._remote_eu_ok(
                "Anywhere in the World",
                "Remote role open to candidates across Europe and the EU",
            )
        )
        self.assertTrue(job_search._remote_eu_ok("Remote Europe", ""))
        self.assertTrue(job_search._remote_eu_ok("EU", ""))

    def test_freshness_filter_drops_old_dates_and_keeps_unknown_dates(self):
        today = date(2026, 9, 25)
        old = {"posted": (today - timedelta(days=30)).isoformat()}
        fresh = {"posted": (today - timedelta(days=2)).isoformat()}
        unknown = {"posted": None}
        kept = job_search.filter_freshness([old, fresh, unknown], 14, today=today)
        self.assertEqual(kept, [fresh, unknown])

    def test_report_caps_each_track_without_hiding_counts(self):
        items = []
        for index in range(5):
            items.append({
                "id": str(index), "title": f"Data Engineer {index}", "company": "Example",
                "location": "Berlin", "country": "de", "remote": False, "url": f"https://example.test/{index}",
                "source": "Fixture", "posted": "2026-09-25", "salary": None,
                "role_fit": "core", "seniority": "mid_plus", "relocation": False,
                "_track": "data_engineer",
            })
        report = job_search.render_report(items, 5, {"Fixture": True}, ["de"], True, 2)
        self.assertIn("3 more Data Engineer results", report)
        self.assertEqual(report.count("| Data Engineer"), 2)

    def test_markdown_cells_escape_pipes_and_newlines(self):
        item = {
            "id": "1",
            "title": "Senior Data Engineer | Analytics",
            "company": "Example\nData",
            "location": "Berlin | Germany",
            "remote": True,
            "relocation": False,
            "role_fit": "core",
            "seniority": "mid_plus",
            "posted": "2026-09-25",
            "salary": None,
            "source": "Fixture",
            "url": "https://example.test/job/1",
            "_track": "data_engineer",
        }
        report = job_search.render_report(
            [item], 1, {"Fixture": True}, ["de"], weekly=True
        )
        self.assertIn("Senior Data Engineer \\| Analytics", report)
        self.assertIn("Example Data", report)
        self.assertIn("Berlin \\| Germany", report)
        self.assertNotIn("Example\nData", report)

    def test_run_never_overwrites_title_track_with_search_keyword(self):
        def fake_eures(keyword, countries):
            title = {
                "data engineer": "Senior Data Engineer",
                "backend engineer": "Senior Backend Engineer",
                "backend developer": "Senior Backend Developer",
                "software engineer": "Senior Software Engineer",
            }.get(keyword, "Senior Data Engineer")
            snippet = "Build backend APIs and data pipelines" if "Backend" in title else "Build data pipelines"
            return [job_search.normalize(
                title=title, company="Example", location="Berlin", country="de",
                remote=False, url=f"https://example.test/{keyword.replace(' ', '-')}",
                source="Fixture", posted="2026-09-25", salary=None, snippet=snippet,
            )]

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "seen.json"
            with patch.object(job_search, "fetch_eures", side_effect=fake_eures), \
                 patch.object(job_search, "fetch_arbeitnow", return_value=[]), \
                 patch.object(job_search, "fetch_remotive", return_value=[]), \
                 patch.object(job_search, "fetch_themuse", return_value=[]), \
                 patch.object(job_search, "fetch_landing", return_value=[]):
                report, _ = job_search.run(
                    countries=["de"], state_path=state_path, weekly=True,
                    adzuna_app_id=None, adzuna_app_key=None, include_remote_boards=False,
                )
            self.assertTrue(state_path.exists())
            self.assertTrue(json.loads(state_path.read_text())["seen"])
        data_section = report.split("### Data Engineer", 1)[1].split("### Backend Engineer", 1)[0]
        backend_section = report.split("### Backend Engineer", 1)[1].split("### Software Engineer", 1)[0]
        self.assertIn("Senior Data Engineer", data_section)
        self.assertIn("Senior Backend Engineer", backend_section)
        self.assertNotIn("Senior Backend Engineer", data_section)

    def test_telegram_delivery_reports_partial_failure(self):
        with patch.object(job_search, "_telegram_send", return_value=None):
            self.assertFalse(job_search.send_telegram("token", "@channel", "report"))

    def test_jobicy_uses_structured_level_description_and_salary(self):
        payload = {"jobs": [{
            "jobTitle": "Backend Engineer",
            "jobLevel": "Senior",
            "jobDescription": "Build APIs and distributed services across Europe",
            "jobExcerpt": "Remote role",
            "companyName": "Example",
            "jobGeo": "Europe",
            "url": "https://example.test/jobicy",
            "pubDate": "2026-09-25",
            "salaryMin": 70000,
            "salaryMax": 90000,
            "salaryCurrency": "EUR",
            "salaryPeriod": "YEARLY",
        }]}
        with patch.object(job_search, "http_get_json", return_value=payload):
            items = job_search.fetch_jobicy()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Senior Backend Engineer")
        self.assertEqual(items[0]["salary"], "€70,000-90,000/year")
        self.assertIn("distributed services", items[0]["description"])

    def test_incomplete_source_does_not_advance_dedupe_state(self):
        item = job_search.normalize(
            title="Senior Data Engineer", company="Example", location="Berlin", country="de",
            remote=False, url="https://example.test/data", source="Fixture",
            posted="2026-09-25", salary=None, snippet="data pipelines",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "seen.json"
            with patch.object(job_search, "fetch_eures", return_value=[item]), \
                 patch.object(job_search, "fetch_arbeitnow", side_effect=RuntimeError("offline")), \
                 patch.object(job_search, "fetch_remotive", return_value=[]), \
                 patch.object(job_search, "fetch_themuse", return_value=[]), \
                 patch.object(job_search, "fetch_landing", return_value=[]):
                job_search.run(
                    countries=["de"], state_path=state_path, weekly=True,
                    adzuna_app_id=None, adzuna_app_key=None, include_remote_boards=False,
                )
            self.assertFalse(state_path.exists())

    def test_duplicate_city_aliases_are_deduped(self):
        first = {
            "id": "one", "title": "Senior Software Engineer", "company": "Example",
            "location": "Köln, Germany", "url": "https://example.test/a", "source": "A",
        }
        second = {
            "id": "two", "title": "Senior Software Engineer", "company": "Example",
            "location": "Cologne, North Rhine-Westphalia, Germany", "url": "https://example.test/b", "source": "B",
        }
        self.assertEqual(len(job_search.dedupe_within_run([first, second])), 1)

    def test_same_source_requisitions_with_same_title_are_not_collapsed(self):
        base = {
            "title": "Senior Backend Engineer", "company": "Example",
            "location": "Berlin", "source": "Adzuna",
        }
        first = {**base, "id": "one", "url": "https://example.test/one"}
        second = {**base, "id": "two", "url": "https://example.test/two"}
        self.assertEqual(len(job_search.dedupe_within_run([first, second])), 2)

    def test_state_alias_keeps_id_stable_when_winner_changes(self):
        item = {
            "id": "url-id", "canonical_url": "https://example.test/new",
            "url": "https://example.test/new", "title": "Data Engineer",
        }
        state = {"seen": {"stable-id": "2026-09-25"}, "aliases": {
            "https://example.test/new": "stable-id",
            "https://example.test/old": "stable-id",
        }}
        job_search.apply_state_aliases([item], state)
        self.assertEqual(item["id"], "stable-id")

    def test_duplicate_url_is_deduped_before_reporting(self):
        base = {
            "id": "one",
            "title": "Senior Data Engineer",
            "company": "Example",
            "location": "Berlin",
            "url": "https://example.test/job/1?utm_source=board",
            "snippet": "data pipelines",
            "source": "A",
        }
        duplicate = {
            **base,
            "id": "two",
            "url": "https://example.test/job/1",
            "source": "B",
        }
        self.assertEqual(len(job_search.dedupe_within_run([base, duplicate])), 1)

    # -- new sources -----------------------------------------------------

    def test_remotive_keeps_eu_remote_and_drops_us(self):
        payload = {"jobs": [
            {
                "title": "Senior Data Engineer", "company_name": "EU Co",
                "candidate_required_location": "Europe",
                "url": "https://remotive.com/jobs/1",
                "publication_date": "2026-09-20T10:00:00",
                "description": "<p>Build <b>Airflow</b> pipelines</p>",
                "salary": "€70k - €90k", "category": "Data",
            },
            {
                "title": "Backend Engineer", "company_name": "US Co",
                "candidate_required_location": "USA",
                "url": "https://remotive.com/jobs/2",
                "publication_date": "2026-09-20T10:00:00",
                "description": "Build APIs", "salary": "", "category": "Software",
            },
        ]}
        with patch.object(job_search, "http_get_json", return_value=payload):
            items = job_search.fetch_remotive("data engineer")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["company"], "EU Co")
        self.assertNotIn("<b>", items[0]["description"])

    def test_themuse_structured_level_and_location(self):
        payload = {"results": [{
            "name": "Data Engineer",
            "company": {"name": "Berlin Data GmbH"},
            "locations": [{"name": "Berlin, Germany"}],
            "levels": [{"name": "Senior Level"}],
            "categories": [{"name": "Data and Analytics"}],
            "refs": {"landing_page": "https://www.themuse.com/jobs/x/1"},
            "publication_date": "2026-09-22T00:00:00Z",
            "contents": "<p>Python, SQL, Airflow</p>",
        }]}
        with patch.object(job_search, "http_get_json", return_value=payload):
            items = job_search.fetch_themuse(categories=("Data and Analytics",), max_pages=1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Senior Data Engineer")
        self.assertEqual(items[0]["seniority"], "mid_plus")

    def test_landing_parses_country_and_salary_and_company_from_url(self):
        payload = [{
            "title": "Senior Backend Engineer",
            "url": "https://landing.jobs/at/inscale/senior-backend-engineer-2026",
            "locations": [{"city": "Lisbon", "country_code": "PT"}],
            "remote": False,
            "published_at": "2026-09-21T09:00:00.000Z",
            "gross_salary_low": 60000, "gross_salary_high": 80000,
            "currency_code": "EUR",
            "role_description": "<li>Python and PostgreSQL</li>",
            "tags": ["Python"],
        }]
        with patch.object(job_search, "http_get_json", return_value=payload):
            items = job_search.fetch_landing(max_pages=1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["company"], "Inscale")
        self.assertEqual(items[0]["country"], "pt")
        self.assertEqual(items[0]["salary"], "€60,000-80,000/year")

    def test_jooble_parses_keyword_source(self):
        payload = {"jobs": [{
            "title": "Backend Developer",
            "location": "Amsterdam",
            "snippet": "Build <b>REST</b> APIs",
            "salary": "€60,000",
            "company": "NL Co",
            "link": "https://jooble.org/jdp/1",
            "updated": "2026-09-19T00:00:00",
        }]}
        with patch.object(job_search, "http_post_json", return_value=payload):
            items = job_search.fetch_jooble("backend engineer", "key")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "Jooble")
        self.assertEqual(items[0]["title"], "Backend Developer")

    def test_hn_comment_parser_requires_track_and_eu_signal(self):
        good = job_search._parse_hn_comment(
            "Example GmbH | Senior Data Engineer | Berlin, Germany | Remote OK | Build pipelines with Python",
            'company <a href="https://example.test/apply">apply</a>',
            "2026-09-15",
        )
        self.assertIsNotNone(good)
        self.assertEqual(good["source"], "HN Hiring")
        self.assertIn("de", good["location"])
        self.assertEqual(good["url"], "https://example.test/apply")
        bad = job_search._parse_hn_comment(
            "Acme | Marketing Manager | Austin, TX", "https://example.test/x", "2026-09-15"
        )
        self.assertIsNone(bad)

    def test_run_with_profile_reports_match_column(self):
        profile = {
            "tracks": ["data_engineer"],
            "seniority_target": ["mid_plus"],
            "preferred_countries": ["de"],
            "skills": {"core": ["python", "sql", "airflow"]},
            "salary": {"target": 60000},
        }
        item = job_search.normalize(
            title="Senior Data Engineer", company="Example", location="Berlin, Germany",
            country="de", remote=False, url="https://example.test/data",
            source="Fixture", posted="2026-09-25", salary="€70,000/year",
            snippet="Python, SQL, Airflow pipelines",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "seen.json"
            with patch.object(job_search, "fetch_eures", return_value=[item]), \
                 patch.object(job_search, "fetch_arbeitnow", return_value=[]), \
                 patch.object(job_search, "fetch_remotive", return_value=[]), \
                 patch.object(job_search, "fetch_themuse", return_value=[]), \
                 patch.object(job_search, "fetch_landing", return_value=[]):
                report, tg = job_search.run(
                    countries=["de"], state_path=state_path, weekly=True,
                    adzuna_app_id=None, adzuna_app_key=None, include_remote_boards=False,
                    profile=profile,
                )
        self.assertIn("| Match |", report)
        self.assertIn("Senior Data Engineer", report)
        self.assertRegex(report, r"[🟢🟡🔴]")


if __name__ == "__main__":
    unittest.main()
