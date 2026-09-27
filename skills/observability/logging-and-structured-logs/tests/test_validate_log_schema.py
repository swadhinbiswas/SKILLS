"""Tests for validate_log_schema.py.

Run from the skill directory:
    python3 -m unittest discover -s tests
"""

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

import validate_log_schema as v  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_log_schema.py"

GOOD = json.dumps({
    "ts": "2026-03-04T14:02:11.418Z",
    "level": "error",
    "service": "checkout-api",
    "version": "4f2a9c",
    "msg": "payment authorization failed",
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    "order": {"id": "ord_88123", "amount_minor": 4200, "currency": "EUR"},
    "error": {"type": "UpstreamTimeout", "message": "timed out after 2000ms"},
    "duration_ms": 2071,
})
INFO = json.dumps({
    "ts": "2026-03-04T14:02:12Z", "level": "info", "service": "checkout-api",
    "msg": "order placed", "trace_id": "abc123", "order_id": "ord_88124",
})
LEAKY = json.dumps({
    "ts": "2026-03-04T14:02:11.418Z", "level": "error", "service": "auth",
    "msg": "login failed", "password": "hunter2", "email": "a@example.com",
})
REDACTED_OK = json.dumps({
    "ts": "2026-03-04T14:02:11.418Z", "level": "error", "service": "auth",
    "msg": "login failed", "password": "[redacted]", "auth_method": "password",
    "token_count": 0, "session_id": None,
})
NO_TS = json.dumps({"level": "info", "service": "a", "msg": "b"})
LOCAL_TS = json.dumps({
    "ts": "2026-03-04T14:02:11.418", "level": "info", "service": "a", "msg": "b"})
BAD_LEVEL = json.dumps({
    "ts": "2026-03-04T14:02:11.418Z", "level": "SEVERE", "service": "a", "msg": "b"})
NULL_MSG = json.dumps({
    "ts": "2026-03-04T14:02:11.418Z", "level": "info", "service": "a", "msg": None})


def scan(lines, **kwargs):
    """Run check_line over a list of raw lines and return the Report."""
    report = v.Report()
    opts = dict(required=["ts", "level", "service", "msg"],
                markers=["[redacted]", "***", "<redacted>", "REDACTED"],
                check_sensitive=True)
    opts.update(kwargs)
    for i, line in enumerate(lines, start=1):
        v.check_line(line, i, "t.log", opts["required"], opts["markers"],
                     opts["check_sensitive"], report)
    return report


class TestFieldValidation(unittest.TestCase):
    def test_clean_line_has_no_findings(self):
        r = scan([GOOD])
        self.assertEqual(r.findings, [])
        self.assertEqual(r.json_lines, 1)
        self.assertEqual(r.level_counts, {"error": 1})

    def test_non_json_line_flagged(self):
        r = scan(["2026-03-04 ERROR checkout failed for order 8831: timeout"])
        cats = r.counts()
        self.assertEqual(cats.get("not_json"), 1)
        self.assertEqual(r.non_json_lines, 1)

    def test_json_array_not_object_flagged(self):
        r = scan(['[1, 2, 3]'])
        self.assertEqual(r.counts().get("not_json_object"), 1)

    def test_missing_required_field(self):
        r = scan([NO_TS])
        cats = r.counts()
        self.assertEqual(cats.get("missing_field"), 1)  # ts only; rest present

    def test_null_required_field(self):
        r = scan([NULL_MSG])
        self.assertEqual(r.counts().get("null_field"), 1)

    def test_custom_required_overrides_default(self):
        r = scan([GOOD], required=["trace_id"])
        self.assertEqual(r.findings, [])

    def test_empty_required_accepts_anything(self):
        r = scan([NO_TS], required=[])
        self.assertEqual(r.findings, [])

    def test_oversized_line_skipped(self):
        report = v.Report()
        lines = v.iter_lines(io.StringIO("x" * 100), report, max_line_bytes=10,
                             path="big.log")
        self.assertEqual(list(lines), [])
        self.assertEqual(report.skipped_oversized, 1)
        self.assertEqual(report.counts().get("oversized_line"), 1)


class TestLevels(unittest.TestCase):
    def test_unknown_level_value(self):
        r = scan([BAD_LEVEL])
        self.assertEqual(r.counts().get("bad_level_value"), 1)

    def test_level_alias_canonicalised_in_counts(self):
        r = scan([json.dumps({"ts": "2026-03-04T14:02:11Z", "level": "WARNING",
                              "service": "a", "msg": "b"})])
        self.assertEqual(r.level_counts, {"warn": 1})

    def test_non_string_level(self):
        r = scan([json.dumps({"ts": "2026-03-04T14:02:11Z", "level": 3,
                              "service": "a", "msg": "b"})])
        self.assertEqual(r.counts().get("bad_level_type"), 1)

    def test_level_filter(self):
        report = v.Report()
        for i, line in enumerate([INFO, LEAKY], start=1):
            v.check_line(line, i, "t.log", ["ts", "level", "service", "msg"],
                         ["[redacted]"], True, report)
        report.findings = [f for f in report.findings
                           if not f.level or v.LEVEL_ORDER.get(f.level, 0) >= 4]
        self.assertTrue(all(f.level in ("", "error") for f in report.findings))
        self.assertTrue(any(f.category == "sensitive_value" for f in report.findings))


class TestTimestamps(unittest.TestCase):
    def test_timestamp_without_offset_flagged(self):
        r = scan([LOCAL_TS])
        self.assertEqual(r.counts().get("bad_timestamp"), 1)

    def test_valid_offsets_accepted(self):
        for ts in ("2026-03-04T14:02:11.418Z", "2026-03-04T14:02:11Z",
                   "2026-03-04T14:02:11+02:00", "2026-03-04T14:02:11.1+0200",
                   "2026-03-04 14:02:11Z"):
            self.assertTrue(v.parse_iso_ts(ts), ts)

    def test_invalid_offsets_rejected(self):
        for ts in ("2026-03-04T14:02:11", "not-a-time", "2026-13-04T14:02:11Z",
                   "2026-03-04T14:02:11.418", ""):
            self.assertFalse(v.parse_iso_ts(ts), ts)


class TestSensitiveDetection(unittest.TestCase):
    def test_password_and_email_flagged(self):
        r = scan([LEAKY])
        details = " ".join(f.detail for f in r.findings
                           if f.category == "sensitive_value")
        self.assertIn("'password'", details)
        self.assertIn("'email'", details)
        self.assertEqual(r.counts()["sensitive_value"], 2)

    def test_redacted_and_allowlisted_values_pass(self):
        r = scan([REDACTED_OK])
        self.assertEqual(r.findings, [])

    def test_ignore_sensitive_flag(self):
        r = scan([LEAKY], check_sensitive=False)
        self.assertEqual(r.findings, [])

    def test_bearer_token_and_dsn_key_detected(self):
        line = json.dumps({
            "ts": "2026-03-04T14:02:11Z", "level": "info", "service": "a",
            "msg": "b", "bearer": "abc.def.ghi", "conn_str": "postgres://u:p@h/db",
        })
        r = scan([line])
        self.assertEqual(r.counts()["sensitive_value"], 2)

    def test_nested_dict_value_redaction(self):
        line = json.dumps({
            "ts": "2026-03-04T14:02:11Z", "level": "info", "service": "a",
            "msg": "b", "creds": {"api_key": "[redacted]", "region": "eu"},
        })
        r = scan([line])
        self.assertEqual(r.findings, [])

    def test_custom_redaction_markers(self):
        line = json.dumps({
            "ts": "2026-03-04T14:02:11Z", "level": "info", "service": "a",
            "msg": "b", "token": "###gone###",
        })
        self.assertEqual(scan([line]).counts()["sensitive_value"], 1)
        self.assertEqual(scan([line], markers=["###gone###"]).findings, [])


class TestRenderAndCli(unittest.TestCase):
    def _run(self, args, stdin_text=None):
        out, err = io.StringIO(), io.StringIO()
        argv = list(args)
        if stdin_text is not None:
            argv = ["-"] + argv
        with redirect_stdout(out), redirect_stderr(err):
            if stdin_text is not None:
                old = sys.stdin
                sys.stdin = io.StringIO(stdin_text)
                try:
                    code = v.main(argv)
                finally:
                    sys.stdin = old
            else:
                code = v.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_exit_zero_on_clean_input(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(GOOD + "\n" + INFO + "\n")
            path = fh.name
        try:
            code, out, _ = self._run([path])
            self.assertEqual(code, 0)
            self.assertIn("OK: no findings", out)
        finally:
            os.unlink(path)

    def test_exit_one_on_findings(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(GOOD + "\n" + LEAKY + "\nnot json at all\n")
            path = fh.name
        try:
            code, out, _ = self._run([path])
            self.assertEqual(code, 1)
            self.assertIn("sensitive_value", out)
            self.assertIn("not_json", out)
        finally:
            os.unlink(path)

    def test_exit_two_on_missing_file(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(["/nonexistent/nope.log"])
        self.assertEqual(ctx.exception.code, 2)

    def test_stdin_mode(self):
        code, out, _ = self._run([], stdin_text=LEAKY + "\n")
        self.assertEqual(code, 1)
        self.assertIn("sensitive_value", out)

    def test_json_output_is_parseable(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(GOOD + "\n" + LEAKY + "\n")
            path = fh.name
        try:
            code, out, _ = self._run([path, "--json"])
            payload = json.loads(out)
            self.assertEqual(code, 1)
            self.assertEqual(payload["json_lines"], 2)
            self.assertEqual(payload["finding_counts"]["sensitive_value"], 2)
            self.assertEqual(payload["files"], [path])
        finally:
            os.unlink(path)

    def test_glob_expansion(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("a.jsonl", "b.jsonl"):
                with open(os.path.join(d, n), "w") as fh:
                    fh.write(GOOD + "\n")
            code, out, _ = self._run([os.path.join(d, "*.jsonl"), "--glob"])
            self.assertEqual(code, 0)
            self.assertIn("2 file(s)", out)

    def test_max_report_truncation_note(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            # 5 identical lines, 2 sensitive fields each -> 10 findings.
            fh.write("".join(LEAKY + "\n" for _ in range(5)))
            path = fh.name
        try:
            _, out, _ = self._run([path, "--max-report", "2"])
            self.assertIn("sensitive_value (10)", out)
            self.assertIn("... and 8 more (use --max-report)", out)
        finally:
            os.unlink(path)


class TestSubprocess(unittest.TestCase):
    """The script must work as an executable, not just as an import."""

    def _run(self, args, stdin_text=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            input=stdin_text, capture_output=True, text=True, timeout=60,
        )

    def test_help_exits_zero(self):
        p = self._run(["--help"])
        self.assertEqual(p.returncode, 0)
        self.assertIn("Exit codes", p.stdout)
        self.assertIn("sensitive", p.stdout.lower())

    def test_stdin_findings_exit_one(self):
        p = self._run(["-"], stdin_text=LEAKY + "\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("sensitive_value", p.stdout)

    def test_stdin_clean_exits_zero(self):
        p = self._run(["-"], stdin_text=GOOD + "\n")
        self.assertEqual(p.returncode, 0)

    def test_bad_level_flag_exits_two(self):
        p = self._run(["-", "--level", "loud"], stdin_text=GOOD + "\n")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--level must be one of", p.stderr)


if __name__ == "__main__":
    unittest.main()
