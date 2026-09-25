import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from telegram_job_formatter import parse_jobs  # noqa: E402


class TelegramFormatterTests(unittest.TestCase):
    def test_parser_handles_software_category_and_level(self):
        raw = "\n".join([
            "<b>💻 Software Engineer</b>",
            '• <b>Senior Software Engineer</b> · (Mid+) · Acme · Berlin · 🏢 On-site/Hybrid · <a href="https://example.test/job">Apply</a>',
        ])
        jobs = parse_jobs(raw)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].category, "Software Engineer")
        self.assertEqual(jobs[0].company, "Acme")
        self.assertEqual(jobs[0].level, "Mid+")

    def test_parser_still_accepts_legacy_line_without_level(self):
        raw = (
            '<b>🛠️ Data Engineer</b>\n'
            '• <b>Data Engineer</b> · Acme · Berlin · 🏢 On-site/Hybrid · '
            '<a href="https://example.test/job">Apply</a>'
        )
        jobs = parse_jobs(raw)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "Acme")
        self.assertEqual(jobs[0].level, "")


if __name__ == "__main__":
    unittest.main()
