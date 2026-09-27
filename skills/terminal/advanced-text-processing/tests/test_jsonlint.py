"""Tests for jsonlint.py. Run: python3 -m unittest discover -s tests"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import jsonlint  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "jsonlint.py"


class ValidateTextTests(unittest.TestCase):
    def test_valid_object(self):
        out = jsonlint.validate_text("t", '{"a": 1, "b": [2, 3]}', reject_duplicates=True, allow_nan=False)
        self.assertTrue(out.ok)
        self.assertEqual(out.value, {"a": 1, "b": [2, 3]})

    def test_invalid_json_reports_line_col(self):
        text = '{\n  "a": 1,\n  "b": ,\n}'
        out = jsonlint.validate_text("t", text, reject_duplicates=True, allow_nan=False)
        self.assertFalse(out.ok)
        self.assertEqual(out.line, 3)
        self.assertIsNotNone(out.col)
        self.assertIsNotNone(out.snippet)

    def test_duplicate_key_rejected_by_default(self):
        text = '{"a": 1, "b": 2, "a": 3}'
        out = jsonlint.validate_text("t", text, reject_duplicates=True, allow_nan=False)
        self.assertFalse(out.ok)
        self.assertIn("duplicate", out.error)

    def test_duplicate_key_allowed_when_requested(self):
        text = '{"a": 1, "a": 2}'
        out = jsonlint.validate_text("t", text, reject_duplicates=False, allow_nan=False)
        self.assertTrue(out.ok)
        self.assertEqual(out.value, {"a": 2})  # last one wins, like json

    def test_nan_rejected_by_default(self):
        text = '{"x": NaN}'
        out = jsonlint.validate_text("t", text, reject_duplicates=True, allow_nan=False)
        self.assertFalse(out.ok)

    def test_nan_allowed_with_no_strict(self):
        text = '{"x": NaN}'
        out = jsonlint.validate_text("t", text, reject_duplicates=True, allow_nan=True)
        self.assertTrue(out.ok)

    def test_bom_reported(self):
        text = '\ufeff{"a": 1}'
        out = jsonlint.validate_text("t", text, reject_duplicates=True, allow_nan=False)
        self.assertFalse(out.ok)
        self.assertIn("BOM", out.error)

    def test_line_col_at(self):
        text = "abc\ndef\nghi"
        self.assertEqual(jsonlint.line_col_at(text, 0), (1, 1))
        self.assertEqual(jsonlint.line_col_at(text, 4), (2, 1))
        self.assertEqual(jsonlint.line_col_at(text, 9), (3, 2))

    def test_describe(self):
        self.assertIn("object", jsonlint.describe({"a": 1}))
        self.assertIn("array", jsonlint.describe([1, 2, 3]))
        self.assertIn("string", jsonlint.describe("hey"))


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content):
        p = self.dir / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def run_main(self, argv):
        so, se = io.StringIO(), io.StringIO()
        with redirect_stdout(so), redirect_stderr(se):
            rc = jsonlint.main(argv)
        return rc, so.getvalue(), se.getvalue()

    def test_single_valid_file_exit0(self):
        p = self.write("ok.json", '{"a": 1}')
        rc, out, _ = self.run_main([p])
        self.assertEqual(rc, 0)
        self.assertIn("OK", out)

    def test_single_invalid_file_exit1(self):
        p = self.write("bad.json", '{"a": }')
        rc, _, err = self.run_main([p])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", err)

    def test_multi_file_mixed_exit1(self):
        good = self.write("good.json", "[1,2,3]")
        bad = self.write("bad.json", "{oops}")
        rc, out, err = self.run_main([good, bad])
        self.assertEqual(rc, 1)
        self.assertIn("OK", out)
        self.assertIn("FAIL", err)

    def test_multi_file_all_good_exit0(self):
        a = self.write("a.json", "1")
        b = self.write("b.json", '"two"')
        rc, _, _ = self.run_main([a, b])
        self.assertEqual(rc, 0)

    def test_missing_file_reports(self):
        rc, _, err = self.run_main([str(self.dir / "nope.json")])
        self.assertEqual(rc, 1)
        self.assertIn("no such file", err)

    def test_directory_reports(self):
        rc, _, err = self.run_main([str(self.dir)])
        self.assertEqual(rc, 1)
        self.assertIn("directory", err)

    def test_quiet_prints_nothing(self):
        p = self.write("ok.json", "1")
        rc, out, err = self.run_main([p, "--quiet"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_stats_mode(self):
        p = self.write("s.json", '{"x": 1, "y": 2}')
        rc, out, _ = self.run_main([p, "--stats"])
        self.assertEqual(rc, 0)
        self.assertIn("object with 2 key", out)

    def test_no_dupe_keys_flag_allows_duplicates(self):
        p = self.write("d.json", '{"a":1,"a":2}')
        rc, _, _ = self.run_main([p, "--no-dupe-keys"])
        self.assertEqual(rc, 0)

    def test_help_exits_clean(self):
        # argparse prints usage then raises SystemExit(0); capture its streams.
        so, se = io.StringIO(), io.StringIO()
        with redirect_stdout(so), redirect_stderr(se):
            with self.assertRaises(SystemExit) as cm:
                jsonlint.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("usage", (so.getvalue() + se.getvalue()).lower())


class SubprocessTests(unittest.TestCase):
    """Exercise the real entry point, exit codes, and stdin handling."""

    def test_exit_code_via_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "ok.json"
            good.write_text("[]")
            r = subprocess.run([sys.executable, str(SCRIPT), str(good)], capture_output=True)
            self.assertEqual(r.returncode, 0)
            bad = Path(d) / "bad.json"
            bad.write_text("{")
            r = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True)
            self.assertEqual(r.returncode, 1)

    def test_stdin_dash(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "-"],
            input=b'{"from":"stdin"}',
            capture_output=True,
        )
        self.assertEqual(r.returncode, 0)

    def test_script_has_shebang_and_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), "script is not executable")
        self.assertEqual(SCRIPT.read_text(encoding="utf-8").splitlines()[0], "#!/usr/bin/env python3")

    def test_stdin_without_dash_argument(self):
        """No positional args and a piped stdin should validate stdin."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input='{"ok": 1}', capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_stdin_without_argument_reports_errors(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input='{"a": 1, "a": 2}', capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("duplicate object key", proc.stdout + proc.stderr)

    def test_explicit_dash_still_works(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "-"],
            input='{"ok": 1}', capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
