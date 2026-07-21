#!/usr/bin/env python3
"""
test_tasks_core.py — Tests for tasks_core.py

Tests all core file operations and recurrence logic in isolation.
All tests use temporary directories — never touches production data.

MAINTENANCE: Update alongside tasks_core.py for every change.

Usage:
    python test_tasks_core.py
    python test_tasks_core.py -v
"""

import sys
import unittest
import tempfile
import shutil
from datetime import datetime
from pathlib import Path

import tasks_core as C

_TMPDIR = None


def setUpModule():
    global _TMPDIR
    _TMPDIR = Path(tempfile.mkdtemp(prefix="core_test_"))


def tearDownModule():
    shutil.rmtree(_TMPDIR, ignore_errors=True)


def _init(subdir: str) -> Path:
    """Create a fresh temp base dir and initialise core."""
    base = _TMPDIR / subdir
    base.mkdir(exist_ok=True)
    C.init(base)
    return base


# ── init ──────────────────────────────────────────────────────────────────────

class TestInit(unittest.TestCase):

    def test_init_creates_tasks_dir(self):
        base = _TMPDIR / "init_test"
        C.init(base)
        self.assertTrue(C.TASKS_DIR.exists())

    def test_require_init_raises_before_init(self):
        C.BASE_DIR = None
        C.TASKS_DIR = None
        with self.assertRaises(RuntimeError):
            C.client_file("test")
        # Re-init so other tests don't break
        _init("after_require")

    def test_client_file_returns_correct_path(self):
        _init(f"cf_{self._testMethodName}")
        path = C.client_file("acme")
        self.assertEqual(path.name, "acme.md")
        self.assertEqual(path.parent, C.TASKS_DIR)

    def test_archive_file_returns_correct_path(self):
        _init(f"af_{self._testMethodName}")
        path = C.archive_file("acme")
        self.assertEqual(path.name, "acme_archive.md")


# ── list_clients ──────────────────────────────────────────────────────────────

class TestListClients(unittest.TestCase):

    def setUp(self):
        _init(f"lc_{self._testMethodName}")

    def test_returns_empty_for_no_clients(self):
        self.assertEqual(C.list_clients(), [])

    def test_excludes_archive_files(self):
        C.client_file("acme").write_text("# Acme\n")
        C.archive_file("acme").write_text("# Archive\n")
        clients = C.list_clients()
        self.assertIn("acme", clients)
        self.assertNotIn("acme_archive", clients)

    def test_returns_sorted(self):
        for name in ["zebra", "acme", "omega"]:
            C.client_file(name).write_text(f"# {name}\n")
        self.assertEqual(C.list_clients(), ["acme", "omega", "zebra"])


# ── save/load last client ─────────────────────────────────────────────────────

class TestLastClient(unittest.TestCase):

    def setUp(self):
        _init(f"last_{self._testMethodName}")

    def test_load_returns_none_when_no_file(self):
        self.assertIsNone(C.load_last_client())

    def test_save_and_load_roundtrip(self):
        C.client_file("acme").write_text("# Acme\n")
        C.save_last_client("acme")
        self.assertEqual(C.load_last_client(), "acme")

    def test_save_none_removes_file(self):
        C.client_file("acme").write_text("# Acme\n")
        C.save_last_client("acme")
        C.save_last_client(None)
        self.assertIsNone(C.load_last_client())

    def test_load_returns_none_if_client_file_gone(self):
        C.client_file("acme").write_text("# Acme\n")
        C.save_last_client("acme")
        C.client_file("acme").unlink()
        self.assertIsNone(C.load_last_client())


# ── parse_task_file ───────────────────────────────────────────────────────────

class TestParseTaskFile(unittest.TestCase):

    def setUp(self):
        _init(f"parse_{self._testMethodName}")

    def _write(self, content: str, client="test") -> dict:
        C.client_file(client).write_text(content)
        return C.parse_task_file(client)

    def test_missing_file_returns_defaults(self):
        data = C.parse_task_file("nonexistent")
        self.assertEqual(data["focuses"], ["General"])
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["descriptions"], {})

    def test_parses_open_task(self):
        data = self._write("# T\n\n## General\n- [ ] #1 Do thing\n")
        t = data["tasks"][0]
        self.assertEqual(t["text"], "Do thing")
        self.assertEqual(t["priority"], 1)
        self.assertEqual(t["status"], "open")
        self.assertFalse(t["done"])

    def test_parses_all_statuses(self):
        data = self._write(
            "# T\n\n## General\n"
            "- [ ] #1 Open\n- [~] #2 InProg\n- [!] #3 Blocked\n- [x] #4 Done\n"
        )
        statuses = {t["text"]: t["status"] for t in data["tasks"]}
        self.assertEqual(statuses["Open"], "open")
        self.assertEqual(statuses["InProg"], "in_progress")
        self.assertEqual(statuses["Blocked"], "blocked")
        self.assertEqual(statuses["Done"], "done")

    def test_parses_due_date(self):
        data = self._write("# T\n\n## General\n- [ ] #1 Task [due 15.06.2026]\n")
        self.assertEqual(data["tasks"][0]["due"], "15.06.2026")
        self.assertEqual(data["tasks"][0]["text"], "Task")

    def test_parses_recur(self):
        data = self._write("# T\n\n## General\n- [ ] #1 Task [due 01.06.2026] [every monday]\n")
        t = data["tasks"][0]
        self.assertEqual(t["recur"], "monday")
        self.assertEqual(t["due"], "01.06.2026")
        self.assertEqual(t["text"], "Task")

    def test_parses_for_and_since(self):
        data = self._write(
            "# T\n\n## General\n- [ ] #1 Task [due 01.06.2026] [for Sarah] [since 20.05.2026]\n"
        )
        t = data["tasks"][0]
        self.assertEqual(t["for"], "Sarah")
        self.assertEqual(t["since"], "20.05.2026")
        self.assertEqual(t["text"], "Task")

    def test_for_and_since_default_none(self):
        data = self._write("# T\n\n## General\n- [ ] #1 Task\n")
        t = data["tasks"][0]
        self.assertIsNone(t["for"])
        self.assertIsNone(t["since"])

    def test_parses_focus_description(self):
        data = self._write("# T\n\n## Platform\n<!-- Platform team support -->\n- [ ] #1 A\n")
        self.assertEqual(data["descriptions"]["Platform"], "Platform team support")

    def test_parses_multiple_focuses(self):
        data = self._write("# T\n\n## General\n- [ ] #1 A\n\n## Platform\n- [ ] #2 B\n")
        self.assertIn("General", data["focuses"])
        self.assertIn("Platform", data["focuses"])


# ── write_task_file ───────────────────────────────────────────────────────────

class TestWriteTaskFile(unittest.TestCase):

    def setUp(self):
        _init(f"write_{self._testMethodName}")

    def test_general_always_first(self):
        data = {
            "focuses": ["Platform", "General"],
            "descriptions": {},
            "tasks": [
                {"priority": 1, "focus": "Platform", "text": "A", "due": None,
                 "recur": None, "done": False, "status": "open"},
                {"priority": 2, "focus": "General", "text": "B", "due": None,
                 "recur": None, "done": False, "status": "open"},
            ]
        }
        C.write_task_file("test", data)
        content = C.client_file("test").read_text()
        general_pos = content.index("## General")
        platform_pos = content.index("## Platform")
        self.assertLess(general_pos, platform_pos)

    def test_repack_priorities(self):
        data = {
            "focuses": ["General"],
            "descriptions": {},
            "tasks": [
                {"priority": 5, "focus": "General", "text": "A", "due": None,
                 "recur": None, "done": False, "status": "open"},
                {"priority": 10, "focus": "General", "text": "B", "due": None,
                 "recur": None, "done": False, "status": "open"},
            ]
        }
        C.write_task_file("test", data)
        parsed = C.parse_task_file("test")
        priorities = sorted(t["priority"] for t in parsed["tasks"] if not t["done"])
        self.assertEqual(priorities, [1, 2])

    def test_done_tasks_excluded(self):
        data = {
            "focuses": ["General"],
            "descriptions": {},
            "tasks": [
                {"priority": 1, "focus": "General", "text": "Open", "due": None,
                 "recur": None, "done": False, "status": "open"},
                {"priority": 2, "focus": "General", "text": "Done", "due": None,
                 "recur": None, "done": True, "status": "done"},
            ]
        }
        C.write_task_file("test", data)
        content = C.client_file("test").read_text()
        self.assertIn("Open", content)
        self.assertNotIn("Done", content)

    def test_writes_focus_description(self):
        data = {
            "focuses": ["Platform"],
            "descriptions": {"Platform": "Platform team"},
            "tasks": [
                {"priority": 1, "focus": "Platform", "text": "Task", "due": None,
                 "recur": None, "done": False, "status": "open"},
            ]
        }
        C.write_task_file("test", data)
        content = C.client_file("test").read_text()
        self.assertIn("<!-- Platform team -->", content)

    def test_roundtrip(self):
        original = {
            "focuses": ["General", "Platform"],
            "descriptions": {"Platform": "Desc"},
            "tasks": [
                {"priority": 1, "focus": "General", "text": "Task A",
                 "due": "15.06.2026", "recur": "weekly", "for": "Sarah",
                 "since": "01.06.2026", "done": False, "status": "open"},
                {"priority": 2, "focus": "Platform", "text": "Task B",
                 "due": None, "recur": None, "for": None, "since": None,
                 "done": False, "status": "blocked"},
            ]
        }
        C.write_task_file("test", original)
        parsed = C.parse_task_file("test")
        open_orig = [t for t in original["tasks"] if not t["done"]]
        open_parsed = [t for t in parsed["tasks"] if not t["done"]]
        self.assertEqual(len(open_orig), len(open_parsed))
        for o, p in zip(
            sorted(open_orig, key=lambda t: t["priority"]),
            sorted(open_parsed, key=lambda t: t["priority"])
        ):
            self.assertEqual(o["text"], p["text"])
            self.assertEqual(o["due"], p["due"])
            self.assertEqual(o["recur"], p["recur"])
            self.assertEqual(o["for"], p["for"])
            self.assertEqual(o["since"], p["since"])
            self.assertEqual(o["status"], p["status"])


# ── archive_task ──────────────────────────────────────────────────────────────

class TestArchiveTask(unittest.TestCase):

    def setUp(self):
        _init(f"arc_{self._testMethodName}")

    def test_creates_archive_file(self):
        task = {"text": "Do thing", "due": None}
        C.archive_task("test", task)
        self.assertTrue(C.archive_file("test").exists())

    def test_appends_task_text(self):
        task = {"text": "Do thing", "due": None}
        C.archive_task("test", task)
        content = C.archive_file("test").read_text()
        self.assertIn("Do thing", content)

    def test_includes_due_date(self):
        task = {"text": "Do thing", "due": "15.06.2026"}
        C.archive_task("test", task)
        content = C.archive_file("test").read_text()
        self.assertIn("15.06.2026", content)

    def test_multiple_tasks_appended(self):
        C.archive_task("test", {"text": "Task A", "due": None})
        C.archive_task("test", {"text": "Task B", "due": None})
        content = C.archive_file("test").read_text()
        self.assertIn("Task A", content)
        self.assertIn("Task B", content)

    def test_includes_for_tag(self):
        task = {"text": "Do thing", "due": None, "for": "Sarah"}
        C.archive_task("test", task)
        content = C.archive_file("test").read_text()
        self.assertIn("[for Sarah]", content)

    def test_omits_for_tag_when_absent(self):
        task = {"text": "Do thing", "due": None}
        C.archive_task("test", task)
        content = C.archive_file("test").read_text()
        self.assertNotIn("[for", content)


# ── parse_archive_file ───────────────────────────────────────────────────────

class TestParseArchiveFile(unittest.TestCase):

    def setUp(self):
        _init(f"parcarc_{self._testMethodName}")

    def test_missing_file_returns_empty(self):
        self.assertEqual(C.parse_archive_file("nonexistent"), [])

    def test_parses_completed_task(self):
        C.archive_task("test", {"text": "Do thing", "due": "15.06.2026", "for": "Sarah"})
        entries = C.parse_archive_file("test")
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["text"], "Do thing")
        self.assertEqual(e["due"], "15.06.2026")
        self.assertEqual(e["for"], "Sarah")
        self.assertIsNotNone(e["completed"])

    def test_parses_task_without_due_or_for(self):
        C.archive_task("test", {"text": "Simple task", "due": None})
        entries = C.parse_archive_file("test")
        self.assertEqual(entries[0]["text"], "Simple task")
        self.assertIsNone(entries[0]["due"])
        self.assertIsNone(entries[0]["for"])

    def test_multiple_entries(self):
        C.archive_task("test", {"text": "Task A", "due": None})
        C.archive_task("test", {"text": "Task B", "due": None})
        entries = C.parse_archive_file("test")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["text"], "Task A")
        self.assertEqual(entries[1]["text"], "Task B")


# ── next_recur_date ───────────────────────────────────────────────────────────

class TestNextRecurDate(unittest.TestCase):

    def a(self, day, month, year):
        return datetime(year, month, day)

    def test_daily(self):
        self.assertEqual(C.next_recur_date("daily", self.a(1,6,2026), self.a(1,6,2026)), "02.06.2026")

    def test_weekly(self):
        self.assertEqual(C.next_recur_date("weekly", self.a(1,6,2026), self.a(1,6,2026)), "08.06.2026")

    def test_fortnightly(self):
        self.assertEqual(C.next_recur_date("fortnightly", self.a(1,6,2026), self.a(1,6,2026)), "15.06.2026")

    def test_monthly_preserves_day(self):
        self.assertEqual(C.next_recur_date("monthly", self.a(15,5,2026), self.a(15,5,2026)), "15.06.2026")

    def test_monthly_year_rollover(self):
        self.assertEqual(C.next_recur_date("monthly", self.a(15,12,2026), self.a(15,12,2026)), "15.01.2027")

    def test_monthly_end_clamp(self):
        self.assertEqual(C.next_recur_date("monthly", self.a(31,1,2026), self.a(31,1,2026)), "28.02.2026")

    def test_3_months(self):
        self.assertEqual(C.next_recur_date("3 months", self.a(1,1,2026), self.a(1,1,2026)), "01.04.2026")

    def test_yearly(self):
        self.assertEqual(C.next_recur_date("yearly", self.a(15,6,2026), self.a(15,6,2026)), "15.06.2027")

    def test_2_weeks(self):
        self.assertEqual(C.next_recur_date("2 weeks", self.a(1,6,2026), self.a(1,6,2026)), "15.06.2026")

    def test_monday(self):
        # 1 Jun 2026 is Monday — next is 8 Jun
        self.assertEqual(C.next_recur_date("monday", self.a(1,6,2026), self.a(1,6,2026)), "08.06.2026")

    def test_friday(self):
        # 1 Jun 2026 Monday — next Friday is 5 Jun
        self.assertEqual(C.next_recur_date("friday", self.a(1,6,2026), self.a(1,6,2026)), "05.06.2026")

    def test_unknown_returns_none(self):
        self.assertIsNone(C.next_recur_date("whenever", self.a(1,6,2026), self.a(1,6,2026)))


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
