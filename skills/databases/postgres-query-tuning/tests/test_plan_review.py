"""Tests for plan_review.py. Run: python -m unittest discover -s tests"""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import plan_review  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "plan_review.py"

# A realistic N+1 plan: inner index scan repeated 50,000 times.
PLAN_N_PLUS_ONE = """
Nested Loop  (cost=0.29..152.00 rows=1000 width=64) (actual time=0.041..98.320 rows=998 loops=1)
  ->  Index Scan using orders_pkey on orders  (cost=0.29..8.31 rows=1 width=64) (actual time=0.010..0.012 rows=1 loops=1)
        Index Cond: (id = 42)
  ->  Index Scan using items_order_idx on items  (cost=0.29..1.98 rows=20 width=64) (actual time=0.029..0.030 rows=1 loops=100000)
        Index Cond: (order_id = orders.id)
Buffers: shared hit=120 read=9400
"""

PLAN_SEQ_SCAN = """
Seq Scan on events  (cost=0.00..185430.00 rows=5200 width=64) (actual time=0.011..88.240 rows=519887 loops=1)
  Filter: (status = 'pending'::text)
  Rows Removed by Filter: 9400000
Buffers: shared hit=100 read=95000
"""

PLAN_BAD_STATS = """
Hash Join  (cost=120.00..4800.00 rows=200 rows=64) (actual time=1200.000..4800.000 rows=918273 loops=1)
  Hash Cond: (o.customer_id = c.id)
  Hash Buckets: 262144  Batches: 4  Memory Usage: 25MB
Buffers: shared hit=300 read=12000
"""

PLAN_CLEAN = """
Index Scan using users_pkey on users  (cost=0.29..8.31 rows=1 width=64) (actual time=0.028..0.030 rows=1 loops=1)
  Index Cond: (id = 1)
Buffers: shared hit=3 read=0
"""

PLAN_NO_ANALYZE = """
Limit  (cost=0.00..4.07 rows=10 width=64)
  ->  Seq Scan on users  (cost=0.00..4.07 rows=10 width=64)
        Filter: (active AND (created_at > '2024-01-01'::timestamp with time zone))
"""


class TestParsePlan(unittest.TestCase):
    def test_parses_nodes(self):
        nodes, warnings = plan_review.parse_plan(PLAN_N_PLUS_ONE)
        self.assertEqual(warnings, [])
        self.assertEqual(len(nodes), 3)
        self.assertTrue(nodes[0].kind.startswith("Nested Loop"))

    def test_extracts_loops_rows_times(self):
        nodes, _ = plan_review.parse_plan(PLAN_N_PLUS_ONE)
        inner = nodes[2]
        self.assertEqual(inner.loops, 100000)
        self.assertEqual(inner.act_rows, 1)
        self.assertAlmostEqual(inner.total_ms, 0.030, places=3)
        self.assertAlmostEqual(inner.total_cost_ms, 3000.0, places=2)

    def test_classifies_index_and_relation(self):
        nodes, _ = plan_review.parse_plan(PLAN_N_PLUS_ONE)
        self.assertEqual(nodes[1].kind, "Index Scan")
        self.assertEqual(nodes[1].index, "orders_pkey")
        self.assertEqual(nodes[1].relation, "orders")

    def test_warns_without_analyze(self):
        nodes, warnings = plan_review.parse_plan(PLAN_NO_ANALYZE)
        self.assertEqual(nodes, [])
        self.assertTrue(warnings)
        self.assertIn("ANALYZE", warnings[0])

    def test_empty_input(self):
        nodes, warnings = plan_review.parse_plan("")
        self.assertEqual(nodes, [])
        self.assertTrue(warnings)

    def test_sort_key_rejected(self):
        # A key line has no actual time and must not become a node.
        nodes, _ = plan_review.parse_plan("  Sort Key: orders.created_at\n")
        self.assertEqual(nodes, [])


class TestAnalyse(unittest.TestCase):
    def _kinds(self, plan):
        return [f.problem.lower() + " " + f.node.lower() for f in plan_review.analyse(plan)]

    def test_detects_n_plus_one(self):
        findings = plan_review.analyse(PLAN_N_PLUS_ONE)
        self.assertTrue(any("100,000 times" in f.problem for f in findings))
        n1 = [f for f in findings if "100,000 times" in f.problem][0]
        self.assertEqual(n1.severity, "high")
        self.assertIn("3,000 ms", n1.problem)

    def test_detects_hash_batch_spill(self):
        findings = plan_review.analyse(PLAN_BAD_STATS)
        self.assertTrue(any("batches" in f.problem.lower() for f in findings))

    def test_detects_stat_estimate_error(self):
        findings = plan_review.analyse(PLAN_BAD_STATS)
        self.assertTrue(any("estimate" in f.problem.lower() for f in findings))

    def test_detects_seq_scan_discarding_rows(self):
        findings = plan_review.analyse(PLAN_SEQ_SCAN)
        self.assertTrue(any("9,400,000" in f.problem for f in findings))
        self.assertEqual(findings[0].severity, "high")

    def test_clean_plan_has_no_high_findings(self):
        findings = plan_review.analyse(PLAN_CLEAN)
        high = [f for f in findings if f.severity == "high"]
        self.assertEqual(high, [], f"unexpected high findings: {high}")

    def test_io_bound_classification(self):
        findings = plan_review.analyse(PLAN_SEQ_SCAN)
        self.assertTrue(any("i/o bound" in f.fix.lower() for f in findings))

    def test_cache_only_plan_says_cpu_bound(self):
        findings = plan_review.analyse(PLAN_CLEAN)
        self.assertTrue(any("cpu" in f.fix.lower() for f in findings))

    def test_findings_sorted_by_severity(self):
        findings = plan_review.analyse(PLAN_N_PLUS_ONE)
        order = {"high": 0, "medium": 1, "low": 2}
        severities = [order[f.severity] for f in findings]
        self.assertEqual(severities, sorted(severities))

    def test_every_finding_has_a_fix(self):
        for plan in (PLAN_N_PLUS_ONE, PLAN_SEQ_SCAN, PLAN_BAD_STATS, PLAN_NO_ANALYZE):
            for f in plan_review.analyse(plan):
                self.assertTrue(f.fix.strip(), f"finding without a fix: {f}")
                self.assertTrue(f.problem.strip(), f"finding without a problem: {f}")


class TestMain(unittest.TestCase):
    def _run(self, argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = plan_review.main(argv)
        finally:
            sys.stdin = old_stdin
        return code, out.getvalue(), err.getvalue()

    def test_stdin_findings_exit_2(self):
        code, out, _ = self._run(["--quiet"], stdin=PLAN_CLEAN)
        self.assertEqual(code, 0)
        self.assertIn("finding", out)

    def test_quiet_suppresses_nonzero_exit(self):
        code, _, _ = self._run(["--quiet"], stdin=PLAN_N_PLUS_ONE)
        self.assertEqual(code, 0)
        code, _, _ = self._run([], stdin=PLAN_N_PLUS_ONE)
        self.assertEqual(code, 2)

    def test_empty_input_is_usage_error(self):
        code, _, err = self._run(["--quiet"], stdin="")
        self.assertEqual(code, 1)
        self.assertIn("empty", err.lower())

    def test_json_output_is_valid(self):
        code, out, _ = self._run(["--json", "--quiet"], stdin=PLAN_SEQ_SCAN)
        data = json.loads(out)
        self.assertIsInstance(data, list)
        self.assertTrue(data)
        self.assertIn("fix", data[0])

    def test_max_findings_respected(self):
        _, out, _ = self._run(["--max-findings", "1", "--quiet"], stdin=PLAN_SEQ_SCAN)
        self.assertEqual(out.count("[HIGH]") + out.count("[MED]") + out.count("[info]"), 1)

    def test_missing_file_is_error(self):
        code, _, err = self._run(["--file", "/nonexistent/plan.txt"])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)

    def test_reads_from_file(self):
        with open(os.devnull, "w"):
            pass
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(PLAN_SEQ_SCAN)
            path = fh.name
        try:
            code, out, _ = self._run(["--file", path, "--quiet"])
            self.assertEqual(code, 0)
            self.assertIn("Seq Scan", out)
        finally:
            os.unlink(path)


class TestSubprocess(unittest.TestCase):
    """The script must work as an actual executable, not just as an import."""

    def test_runs_as_executable(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--quiet"],
            input=PLAN_CLEAN,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("finding", proc.stdout)

    def test_no_input_is_usage_error(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--file", proc.stdout)
        self.assertIn("EXPLAIN", proc.stdout)


if __name__ == "__main__":
    unittest.main()
