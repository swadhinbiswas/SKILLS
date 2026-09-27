"""Tests for search_across_internet.py. Run: python -m unittest discover -s tests"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import search_across_internet as s  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "search_across_internet.py"

GH_REPO = {
    "full_name": "obra/superpowers",
    "html_url": "https://github.com/obra/superpowers",
    "description": "An agentic skills framework and software development methodology",
    "stargazers_count": 292000,
    "topics": ["agent-skills", "claude"],
    "pushed_at": "2026-09-20T10:00:00Z",
}
UNRELATED = {
    "full_name": "torvalds/linux",
    "html_url": "https://github.com/torvalds/linux",
    "description": "Linux kernel source tree",
    "stargazers_count": 180000,
    "topics": ["linux"],
    "pushed_at": "2026-09-25T10:00:00Z",
}


class TestScoring(unittest.TestCase):
    def test_skill_md_bonus(self):
        r = s.Result(full_name="a/b", url="u", has_skill_md=True, skill_count=3)
        s.score_result(r, ["x"])
        self.assertTrue(any("SKILL.md" in x for x in r.reasons))
        self.assertGreater(r.score, 0)

    def test_name_match_beats_description_match(self):
        in_name = s.Result(full_name="acme/k8s-helper", url="u", description="tools")
        in_desc = s.Result(full_name="acme/tool", url="u", description="a k8s helper")
        s.score_result(in_name, ["k8s"])
        s.score_result(in_desc, ["k8s"])
        self.assertGreater(in_name.score, in_desc.score)

    def test_stars_break_ties_but_do_not_dominate(self):
        popular = s.Result(full_name="a/b", url="u", stars=100000,
                           description="x k8s helper")
        obscure = s.Result(full_name="c/d", url="u", stars=3,
                           description="x k8s helper")
        s.score_result(popular, ["k8s"])
        s.score_result(obscure, ["k8s"])
        # Popular should win, but not by a landslide.
        self.assertGreater(popular.score, obscure.score)
        self.assertLess(popular.score - obscure.score, 25)

    def test_score_is_capped_at_100(self):
        r = s.Result(full_name="k8s/k8s-helper", url="u", stars=500000,
                     has_skill_md=True, skill_count=50,
                     description="k8s helper for k8s")
        s.score_result(r, ["k8s", "helper"])
        self.assertLessEqual(r.score, 100.0)

    def test_no_match_scores_low(self):
        r = s.Result(full_name="a/b", url="u", description="nothing relevant")
        s.score_result(r, ["kubernetes"])
        self.assertLess(r.score, 30)


class TestLocalSearch(unittest.TestCase):
    def _write_catalog(self, tmp: str, entries: list[dict]) -> Path:
        root = Path(tmp)
        (root / "skills").mkdir(parents=True, exist_ok=True)
        catalog = root / "skills" / "catalog.json"
        catalog.write_text(json.dumps({"skills": entries}), encoding="utf-8")
        return catalog

    def test_finds_matching_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_catalog(tmp, [{
                "name": "flaky-test-triage", "domain": "debugging",
                "path": "debugging/flaky-test-triage",
                "description": "Diagnose tests that pass intermittently",
            }])
            with mock.patch.object(s, "find_catalog", return_value=Path(tmp) / "skills" / "catalog.json"):
                results = s.collect_local("flaky test", 5)
        self.assertEqual(len(results), 1)
        self.assertIn("flaky-test-triage", results[0].full_name)
        self.assertTrue(results[0].url.endswith("SKILL.md"))

    def test_no_match_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_catalog(tmp, [{
                "name": "a", "domain": "d", "path": "d/a", "description": "unrelated",
            }])
            with mock.patch.object(s, "find_catalog", return_value=Path(tmp) / "skills" / "catalog.json"):
                results = s.collect_local("kubernetes", 5)
        self.assertEqual(results, [])

    def test_matches_on_path_even_if_description_is_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_catalog(tmp, [{
                "name": "kubernetes-debugging", "domain": "kubernetes",
                "path": "kubernetes/kubernetes-debugging", "description": "...",
            }])
            with mock.patch.object(s, "find_catalog", return_value=Path(tmp) / "skills" / "catalog.json"):
                results = s.collect_local("kubernetes", 5)
        self.assertEqual(len(results), 1)

    def test_missing_catalog_is_not_fatal(self):
        with mock.patch.object(s, "find_catalog", return_value=None):
            self.assertEqual(s.collect_local("anything", 5), [])

    def test_real_repo_catalog_is_usable(self):
        results = s.collect_local("kubernetes", 5)
        self.assertTrue(results, "expected skills in the real catalog")
        self.assertTrue(all(r.sources == ["local"] for r in results))
        for r in results:
            self.assertTrue(Path(r.url).is_file(), f"dangling path: {r.url}")


class TestMainLocal(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = s.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_local_mode_returns_zero(self):
        code, out, _ = self._run(["--local", "kubernetes"])
        self.assertEqual(code, 0)
        self.assertIn("result(s)", out)

    def test_json_output_parses(self):
        code, out, _ = self._run(["--local", "--json", "postgres"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIsInstance(data, list)
        self.assertTrue(data)
        self.assertIn("score", data[0])

    def test_no_query_is_usage_error(self):
        old = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            code, _, err = self._run([])
        finally:
            sys.stdin = old
        self.assertEqual(code, 1)
        self.assertIn("no query", err)

    def test_bad_limit_rejected(self):
        code, _, err = self._run(["--local", "--limit", "0", "x"])
        self.assertEqual(code, 1)
        self.assertIn("--limit", err)

    def test_stdin_query_is_accepted(self):
        old = sys.stdin
        sys.stdin = io.StringIO("kubernetes")
        try:
            code, out, _ = self._run(["--local"])
        finally:
            sys.stdin = old
        self.assertEqual(code, 0)
        self.assertIn("result(s)", out)


class TestHttpHandling(unittest.TestCase):
    def test_rate_limit_returns_helpful_error(self):
        with mock.patch.object(s, "http_get", return_value=(403, b"{}", {})):
            items, err = s.search_repos("test", "")
        self.assertEqual(items, [])
        self.assertIn("rate limit", err.lower())
        self.assertIn("GITHUB_TOKEN", err)

    def test_422_is_reported(self):
        with mock.patch.object(s, "http_get", return_value=(422, b"{}", {})):
            items, err = s.search_repos("###", "")
        self.assertEqual(items, [])
        self.assertIn("422", err)

    def test_network_error_propagates(self):
        with mock.patch.object(s, "http_get", side_effect=ConnectionError("boom")):
            with self.assertRaises(ConnectionError):
                s.search_repos("test", "")

    def test_token_from_environment(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "abc"}, clear=False):
            self.assertEqual(s.get_token(), "abc")
        with mock.patch.dict(os.environ, {"GH_TOKEN": "def"}, clear=True):
            self.assertEqual(s.get_token(), "def")

    def test_no_token_is_empty_string(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(s.get_token(), "")


class TestCollectFromGithub(unittest.TestCase):
    def test_dedupes_across_strategies(self):
        def fake_search(q, token, per_page=20, sort="stars"):
            return [GH_REPO, UNRELATED], None
        with mock.patch.object(s, "search_repos", side_effect=fake_search):
            results, _ = s.collect_from_github("agent skills", "", 10, deep=False)
        names = [r.full_name for r in results]
        self.assertEqual(len(names), len(set(names)), "duplicate repos returned")
        self.assertIn("obra/superpowers", names)

    def test_records_multiple_sources(self):
        def fake_search(q, token, per_page=20, sort="stars"):
            return [GH_REPO], None
        with mock.patch.object(s, "search_repos", side_effect=fake_search):
            results, _ = s.collect_from_github("agent skills", "", 10, deep=False)
        self.assertGreater(len(results[0].sources), 1)

    def test_deep_mode_counts_skill_md(self):
        def fake_search(q, token, per_page=20, sort="stars"):
            return [GH_REPO], None
        with mock.patch.object(s, "search_repos", side_effect=fake_search), \
             mock.patch.object(s, "repo_tree_has_skill", return_value=(True, 7)):
            results, _ = s.collect_from_github("skills", "", 5, deep=True)
        self.assertTrue(results[0].has_skill_md)
        self.assertEqual(results[0].skill_count, 7)

    def test_respects_limit(self):
        def fake_search(q, token, per_page=20, sort="stars"):
            return [GH_REPO, UNRELATED], None
        with mock.patch.object(s, "search_repos", side_effect=fake_search):
            results, _ = s.collect_from_github("x", "", 1, deep=False)
        self.assertLessEqual(len(results), 1)

    def test_errors_become_notes_not_crashes(self):
        with mock.patch.object(s, "search_repos", return_value=([], "rate limited")):
            results, notes = s.collect_from_github("x", "", 5, deep=False)
        self.assertEqual(results, [])
        self.assertTrue(notes)


class TestRender(unittest.TestCase):
    def test_empty_suggests_broader_query(self):
        out = s.render([], "zzz", [])
        self.assertIn("No skills found", out)
        self.assertIn("include-local", out)

    def test_notes_are_appended(self):
        r = s.Result(full_name="a/b", url="https://x", description="d", stars=5)
        out = s.render([r], "q", ["a note"])
        self.assertIn("a note", out)
        self.assertIn("https://x", out)


class TestSubprocess(unittest.TestCase):
    def test_help_works(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--local", proc.stdout)
        self.assertIn("GITHUB_TOKEN", proc.stdout)

    def test_runs_as_executable(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--local", "kubernetes"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("local/kubernetes", proc.stdout)

    def test_executable_bit_is_set(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), "script is not executable")


if __name__ == "__main__":
    unittest.main()
