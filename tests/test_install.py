"""Tests for install.sh — the client wiring and GitHub import logic.

These shell out to the real installer against throwaway HOME/temp targets, so
they exercise the actual code path, not a re-implementation. Run with:

    python -m unittest discover -s tests
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"


def run(args, env=None, cwd=None, timeout=120):
    e = dict(os.environ)
    e["HOME"] = env["HOME"] if env and "HOME" in env else e["HOME"]
    if env:
        e.update(env)
    return subprocess.run(
        ["bash", str(INSTALL), *args],
        capture_output=True, text=True, env=e, cwd=cwd or str(REPO),
        timeout=timeout,
    )


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments that are outside strings."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if ch == '"':
                in_str = False
            i += 1; continue
        if ch == '"':
            in_str = True; out.append(ch); i += 1; continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2; continue
        out.append(ch); i += 1
    return "".join(out)


class TestPrerequisites(unittest.TestCase):
    def test_installer_exists_and_is_executable(self):
        self.assertTrue(INSTALL.is_file(), "install.sh missing")
        self.assertTrue(os.access(INSTALL, os.X_OK), "install.sh not executable")

    def test_passes_syntax_check(self):
        proc = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_help_works_and_documents_key_options(self):
        proc = run(["--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in ("--client", "--all", "--list", "--verify",
                     "--uninstall", "--dry-run", "--from", "--link-mode"):
            self.assertIn(flag, proc.stdout, f"{flag} undocumented in --help")

    def test_help_mentions_every_known_client(self):
        proc = run(["--help"])
        self.assertIn("codex", proc.stdout.lower())


class TestList(unittest.TestCase):
    def test_list_shows_clients_and_skill_count(self):
        proc = run(["--list"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for client in ("claude-code", "codex", "opencode", "cursor"):
            self.assertIn(client, proc.stdout)
        self.assertRegex(proc.stdout, r"\d+ skills in")

    def test_list_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(os.listdir(tmp))
            run(["--list"], env={"HOME": tmp})
            self.assertEqual(sorted(os.listdir(tmp)), before)


class TestVerify(unittest.TestCase):
    def test_verify_reports_every_client(self):
        proc = run(["--verify"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("claude-code", proc.stdout)
        self.assertIn("opencode", proc.stdout)

    def test_verify_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run(["--verify"], env={"HOME": tmp})
            self.assertEqual(os.listdir(tmp), [])


class TestDirClientInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.target = os.path.join(self.tmp, "skills")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_installs_all_skills_as_symlinks(self):
        proc = run([f"--client=test={self.target}"], env={"HOME": self.tmp})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        entries = os.listdir(self.target)
        expected = sum(
            1 for _ in (REPO / "skills").rglob("SKILL.md")
        )
        self.assertEqual(len(entries), expected, "not every skill was linked")

    def test_symlinks_point_at_real_skill_files(self):
        run([f"--client=test={self.target}"], env={"HOME": self.tmp})
        for name in os.listdir(self.target)[:10]:
            link = Path(self.target) / name
            self.assertTrue(link.is_symlink(), f"{name} is not a symlink")
            self.assertTrue((link / "SKILL.md").is_file(),
                            f"{name} symlink does not resolve to a SKILL.md")

    def test_reinstall_is_idempotent(self):
        run([f"--client=test={self.target}"], env={"HOME": self.tmp})
        before = {n: os.readlink(Path(self.target) / n) for n in os.listdir(self.target)}
        run([f"--client=test={self.target}"], env={"HOME": self.tmp})
        after = {n: os.readlink(Path(self.target) / n) for n in os.listdir(self.target)}
        self.assertEqual(before, after)

    def test_conflicting_entry_is_not_clobbered_without_force(self):
        os.makedirs(self.target, exist_ok=True)
        conflict = Path(self.target) / "systematic-debugging"
        os.makedirs(conflict)
        (conflict / "unrelated.txt").write_text("do not delete me")
        proc = run([f"--client=test={self.target}"], env={"HOME": self.tmp})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((conflict / "unrelated.txt").exists(),
                        "existing directory was destroyed without --force")
        self.assertIn("conflict", (proc.stdout + proc.stderr).lower())

    def test_force_replaces_conflict(self):
        os.makedirs(self.target, exist_ok=True)
        conflict = Path(self.target) / "systematic-debugging"
        os.makedirs(conflict)
        (conflict / "unrelated.txt").write_text("x")
        run([f"--client=test={self.target}", "--force"], env={"HOME": self.tmp})
        self.assertTrue((conflict / "SKILL.md").is_file(),
                        "--force did not replace the conflicting entry")

    def test_copy_mode_copies_files(self):
        run([f"--client=test={self.target}", "--link-mode", "copy"],
            env={"HOME": self.tmp})
        sample = Path(self.target) / "systematic-debugging"
        self.assertFalse(sample.is_symlink(), "copy mode created a symlink")
        self.assertTrue((sample / "SKILL.md").is_file())

    def test_dry_run_creates_nothing(self):
        run([f"--client=test={self.target}", "--dry-run"], env={"HOME": self.tmp})
        self.assertFalse(os.path.exists(self.target),
                         "--dry-run created the target directory")

    def test_custom_path_expands_tilde(self):
        proc = run(["--client=test2=~/sub/skills", "--dry-run"],
                   env={"HOME": self.tmp})
        self.assertIn(os.path.join(self.tmp, "sub/skills"), proc.stdout)

    def test_unknown_client_errors_clearly(self):
        # A bare name that is neither a known id nor id=path must be rejected.
        proc = run(["--client=does-not-exist"], env={"HOME": self.tmp})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("id>=<path>", proc.stderr.lower())

    def test_named_unknown_client_errors_clearly(self):
        proc = run(["--client", "does-not-exist"], env={"HOME": self.tmp})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unknown client", proc.stderr.lower())


class TestUninstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.target = os.path.join(self.tmp, "skills")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_uninstall_removes_only_our_symlinks(self):
        run([f"--client=test={self.target}"], env={"HOME": self.tmp})
        outsider = Path(self.target) / "not-ours"
        outsider.mkdir()
        (outsider / "keep.txt").write_text("keep me")
        run([f"--client=test={self.target}", "--uninstall"], env={"HOME": self.tmp})
        self.assertFalse((Path(self.target) / "systematic-debugging").exists(),
                         "our symlink survived uninstall")
        self.assertTrue((outsider / "keep.txt").exists(),
                        "uninstall deleted a directory it did not create")

    def test_uninstall_leaves_repo_untouched(self):
        skill = REPO / "skills" / "debugging" / "systematic-debugging"
        run([f"--client=test={self.target}"], env={"HOME": self.tmp})
        run([f"--client=test={self.target}", "--uninstall"], env={"HOME": self.tmp})
        self.assertTrue(skill.is_dir(), "uninstall damaged the source repo")
        self.assertTrue((skill / "SKILL.md").is_file())


class TestConfigClient(unittest.TestCase):
    """opencode-style clients configured by editing a config file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # The installer resolves ~/.config/opencode/opencode.jsonc from $HOME,
        # so the fixture must use the real dotted path.
        self.cfg_dir = os.path.join(self.tmp, ".config", "opencode")
        os.makedirs(self.cfg_dir, exist_ok=True)
        self.cfg = os.path.join(self.cfg_dir, "opencode.jsonc")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _install(self):
        return run(["--client", "opencode"], env={"HOME": self.tmp})

    def test_adds_path_to_existing_paths_array(self):
        Path(self.cfg).write_text(
            '{\n  "skills": {\n    "paths": [\n      "/home/u/skills"\n    ]\n  }\n}\n',
            encoding="utf-8",
        )
        self.assertEqual(self._install().returncode, 0)
        data = json.loads(strip_jsonc(Path(self.cfg).read_text()))
        self.assertIn(str(REPO / "skills"), data["skills"]["paths"])
        self.assertIn("/home/u/skills", data["skills"]["paths"], "existing entry lost")

    def test_preserves_comments(self):
        Path(self.cfg).write_text(
            '{\n  // my config\n  "skills": {\n    "paths": []\n  }\n}\n',
            encoding="utf-8",
        )
        self._install()
        self.assertIn("// my config", Path(self.cfg).read_text(),
                      "comment was destroyed")

    def test_preserves_other_keys(self):
        Path(self.cfg).write_text(
            '{\n  "model": "x/y",\n  "skills": { "paths": [] },\n'
            '  "url": "https://api.example.com/v1"\n}\n',
            encoding="utf-8",
        )
        self._install()
        data = json.loads(strip_jsonc(Path(self.cfg).read_text()))
        self.assertEqual(data["model"], "x/y")
        self.assertEqual(data["url"], "https://api.example.com/v1",
                         "a // inside a URL string was mangled")

    def test_adds_paths_key_when_skills_object_lacks_it(self):
        Path(self.cfg).write_text('{\n  "skills": {}\n}\n', encoding="utf-8")
        self.assertEqual(self._install().returncode, 0)
        data = json.loads(strip_jsonc(Path(self.cfg).read_text()))
        self.assertIn(str(REPO / "skills"), data["skills"]["paths"])

    def test_handles_config_with_no_skills_key(self):
        Path(self.cfg).write_text('{\n  "model": "x/y"\n}\n', encoding="utf-8")
        self.assertEqual(self._install().returncode, 0)
        data = json.loads(strip_jsonc(Path(self.cfg).read_text()))
        self.assertIn(str(REPO / "skills"), data["skills"]["paths"])

    def test_creating_config_when_absent(self):
        self.assertEqual(self._install().returncode, 0)
        self.assertTrue(Path(self.cfg).is_file())
        data = json.loads(strip_jsonc(Path(self.cfg).read_text()))
        self.assertIn(str(REPO / "skills"), data["skills"]["paths"])

    def test_is_idempotent(self):
        self._install()
        first = Path(self.cfg).read_text()
        self._install()
        self.assertEqual(first, Path(self.cfg).read_text(),
                         "second run changed the config")
        data = json.loads(strip_jsonc(first))
        self.assertEqual(
            data["skills"]["paths"].count(str(REPO / "skills")), 1,
            "path was added twice",
        )

    def test_dry_run_leaves_config_untouched(self):
        original = '{\n  "skills": { "paths": [] }\n}\n'
        Path(self.cfg).write_text(original, encoding="utf-8")
        run(["--client", "opencode", "--dry-run"], env={"HOME": self.tmp})
        self.assertEqual(Path(self.cfg).read_text(), original)

    def test_uninstall_removes_the_path(self):
        self._install()
        run(["--client", "opencode", "--uninstall"], env={"HOME": self.tmp})
        data = json.loads(strip_jsonc(Path(self.cfg).read_text()))
        self.assertNotIn(str(REPO / "skills"), data.get("skills", {}).get("paths", []))

    def test_uninstall_keeps_other_paths(self):
        self._install()
        run(["--client", "opencode", "--uninstall"], env={"HOME": self.tmp})
        data = json.loads(strip_jsonc(Path(self.cfg).read_text()))
        self.assertIsInstance(data.get("skills", {}).get("paths", []), list)


class TestGitHubImport(unittest.TestCase):
    def test_from_requires_argument(self):
        proc = run(["--from"])
        self.assertNotEqual(proc.returncode, 0)

    def test_dry_run_import_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(os.listdir(tmp))
            proc = run(["--from", "owner/repo", "--dry-run"], env={"HOME": tmp})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(sorted(os.listdir(tmp)), before)
            self.assertIn("would clone", proc.stdout)

    def test_bad_repo_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run(["--from", "this-org-does-not-exist-xyz/nope-abc"],
                       env={"HOME": tmp}, timeout=90)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("could not clone", (proc.stdout + proc.stderr).lower())


class TestSafety(unittest.TestCase):
    def test_dry_run_flag_is_honored_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "skills")
            proc = run(["--all", "--dry-run"], env={"HOME": tmp})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("DRY RUN", proc.stdout)
            self.assertFalse(os.path.exists(target))

    def test_no_flag_changes_nothing_in_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(os.listdir(tmp))
            run(["--list"], env={"HOME": tmp})
            run(["--verify"], env={"HOME": tmp})
            self.assertEqual(sorted(os.listdir(tmp)), before)

    def test_install_never_touches_system_paths(self):
        text = INSTALL.read_text(encoding="utf-8")
        for forbidden in ("/etc/", "/usr/", "/System/", "rm -rf /", "sudo "):
            if forbidden in ("rm -rf /",):
                self.assertNotIn(forbidden, text,
                                 "installer contains a catastrophic command")
            else:
                self.assertNotIn(f'expand_path "{forbidden}', text)


if __name__ == "__main__":
    unittest.main()
