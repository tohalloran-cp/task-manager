#!/usr/bin/env python3
"""
test_tasks.py — Smoke tests for tasks.py

Runs entirely in /tmp — never touches production data.
API and Termux calls are mocked.

MAINTENANCE: This file must be updated alongside tasks.py.
Every new feature needs tests. Every bug fix needs a regression test.

Usage:
    python test_tasks.py
    python test_tasks.py -v    # verbose
"""

import sys
import unittest
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── Patch production paths before importing tasks ─────────────────────────────
# We redirect TASKS_DIR, BASE_DIR etc to a temp directory so tests never
# touch /storage/emulated/0/Documents/ai-assistant

_TMPDIR = None  # set in setUpModule


def setUpModule():
    global _TMPDIR
    _TMPDIR = Path(tempfile.mkdtemp(prefix="tasks_test_"))


def tearDownModule():
    shutil.rmtree(_TMPDIR, ignore_errors=True)


# Patch paths before import
import unittest.mock as mock
_path_patch = mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
_path_patch.start()

import importlib, types

# We import tasks but immediately override its directory globals
# so all file operations go to /tmp
import tasks as T


import tasks_core as C

def _set_tmp_dirs(tmp: Path):
    """Redirect tasks module file paths to tmp directory.

    Deliberately does NOT reassign T.client_file / T.archive_task / etc.
    Those are convenience wrappers defined in tasks.py itself that delegate
    to tasks_core — reassigning them here would bypass the actual wrapper
    code and mask bugs in it (e.g. a wrapper whose signature falls out of
    sync with the function it forwards to). tasks_core.init(tmp) already
    redirects the shared BASE_DIR/TASKS_DIR that every wrapper reads
    through, so no reassignment is needed for isolation.
    """
    C.init(tmp)
    T.BASE_DIR  = tmp


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_task(priority=1, text="Test task", focus="General", due=None,
              recur=None, status="open", done=False):
    return {
        "priority": priority,
        "text": text,
        "focus": focus,
        "due": due,
        "recur": recur,
        "status": status,
        "done": done,
    }


def make_data(*tasks, focuses=None, descriptions=None):
    return {
        "focuses": focuses or ["General"],
        "descriptions": descriptions or {},
        "tasks": list(tasks),
    }


# ── Test classes ──────────────────────────────────────────────────────────────

class TestRecurrenceDates(unittest.TestCase):
    """next_recur_date — all patterns and edge cases."""

    def _anchor(self, day, month, year):
        return datetime(year, month, day)

    def test_daily(self):
        anchor = self._anchor(1, 6, 2026)
        result = T.next_recur_date("daily", anchor, anchor)
        self.assertEqual(result, "02.06.2026")

    def test_weekly(self):
        anchor = self._anchor(1, 6, 2026)  # Monday
        result = T.next_recur_date("weekly", anchor, anchor)
        self.assertEqual(result, "08.06.2026")

    def test_fortnightly(self):
        anchor = self._anchor(1, 6, 2026)
        result = T.next_recur_date("fortnightly", anchor, anchor)
        self.assertEqual(result, "15.06.2026")

    def test_monthly_preserves_day(self):
        anchor = self._anchor(15, 5, 2026)
        result = T.next_recur_date("monthly", anchor, anchor)
        self.assertEqual(result, "15.06.2026")

    def test_monthly_year_rollover(self):
        anchor = self._anchor(15, 12, 2026)
        result = T.next_recur_date("monthly", anchor, anchor)
        self.assertEqual(result, "15.01.2027")

    def test_monthly_end_of_month_clamp(self):
        # 31 Jan -> Feb has no 31st, should clamp to 28th
        anchor = self._anchor(31, 1, 2026)
        result = T.next_recur_date("monthly", anchor, anchor)
        self.assertEqual(result, "28.02.2026")

    def test_3_months(self):
        anchor = self._anchor(1, 1, 2026)
        result = T.next_recur_date("3 months", anchor, anchor)
        self.assertEqual(result, "01.04.2026")

    def test_6_months(self):
        anchor = self._anchor(1, 1, 2026)
        result = T.next_recur_date("6 months", anchor, anchor)
        self.assertEqual(result, "01.07.2026")

    def test_yearly(self):
        anchor = self._anchor(15, 6, 2026)
        result = T.next_recur_date("yearly", anchor, anchor)
        self.assertEqual(result, "15.06.2027")

    def test_2_weeks(self):
        anchor = self._anchor(1, 6, 2026)
        result = T.next_recur_date("2 weeks", anchor, anchor)
        self.assertEqual(result, "15.06.2026")

    def test_monday(self):
        # 1 Jun 2026 is a Monday — next Monday is 8 Jun
        anchor = self._anchor(1, 6, 2026)
        result = T.next_recur_date("monday", anchor, anchor)
        self.assertEqual(result, "08.06.2026")

    def test_friday(self):
        # 1 Jun 2026 is Monday — next Friday is 5 Jun
        anchor = self._anchor(1, 6, 2026)
        result = T.next_recur_date("friday", anchor, anchor)
        self.assertEqual(result, "05.06.2026")

    def test_unknown_returns_none(self):
        anchor = self._anchor(1, 6, 2026)
        result = T.next_recur_date("whenever", anchor, anchor)
        self.assertIsNone(result)


class TestParseRoundTrip(unittest.TestCase):
    """parse_task_file / write_task_file round-trip."""

    def setUp(self):
        self.tmp = _TMPDIR / "roundtrip"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"

    def _write_and_parse(self, content: str) -> dict:
        T.client_file(self.client).write_text(content)
        return T.parse_task_file(self.client)

    def test_basic_open_task(self):
        data = self._write_and_parse(
            "# Test\n\n## General\n- [ ] #1 Do something\n"
        )
        self.assertEqual(len(data["tasks"]), 1)
        t = data["tasks"][0]
        self.assertEqual(t["text"], "Do something")
        self.assertEqual(t["priority"], 1)
        self.assertEqual(t["status"], "open")
        self.assertFalse(t["done"])

    def test_all_statuses_parsed(self):
        data = self._write_and_parse(
            "# Test\n\n## General\n"
            "- [ ] #1 Open task\n"
            "- [~] #2 In progress\n"
            "- [!] #3 Blocked\n"
            "- [x] #4 Done\n"
        )
        statuses = {t["text"]: t["status"] for t in data["tasks"]}
        self.assertEqual(statuses["Open task"], "open")
        self.assertEqual(statuses["In progress"], "in_progress")
        self.assertEqual(statuses["Blocked"], "blocked")
        self.assertEqual(statuses["Done"], "done")

    def test_due_date_parsed(self):
        data = self._write_and_parse(
            "# Test\n\n## General\n- [ ] #1 Task with date [due 15.06.2026]\n"
        )
        self.assertEqual(data["tasks"][0]["due"], "15.06.2026")
        self.assertEqual(data["tasks"][0]["text"], "Task with date")

    def test_recur_parsed(self):
        data = self._write_and_parse(
            "# Test\n\n## General\n- [ ] #1 Weekly task [due 01.06.2026] [every monday]\n"
        )
        t = data["tasks"][0]
        self.assertEqual(t["recur"], "monday")
        self.assertEqual(t["due"], "01.06.2026")
        self.assertEqual(t["text"], "Weekly task")

    def test_focus_description_parsed(self):
        data = self._write_and_parse(
            "# Test\n\n## Platform\n<!-- Supporting the platform team -->\n- [ ] #1 A task\n"
        )
        self.assertEqual(data["descriptions"]["Platform"], "Supporting the platform team")

    def test_multiple_focuses(self):
        data = self._write_and_parse(
            "# Test\n\n## General\n- [ ] #1 Task A\n\n## Platform\n- [ ] #2 Task B\n"
        )
        self.assertEqual(len(data["focuses"]), 2)
        self.assertIn("General", data["focuses"])
        self.assertIn("Platform", data["focuses"])

    def test_write_then_parse_roundtrip(self):
        """write_task_file followed by parse_task_file returns equivalent data."""
        original = make_data(
            make_task(1, "First task", "General", due="15.06.2026"),
            make_task(2, "Second task", "Platform", recur="weekly"),
            make_task(3, "Blocked task", "General", status="blocked"),
            focuses=["General", "Platform"],
        )
        T.write_task_file(self.client, original)
        parsed = T.parse_task_file(self.client)

        open_orig = [t for t in original["tasks"] if not t["done"]]
        open_parsed = [t for t in parsed["tasks"] if not t["done"]]
        self.assertEqual(len(open_orig), len(open_parsed))

        for o, p in zip(sorted(open_orig, key=lambda t: t["priority"]),
                        sorted(open_parsed, key=lambda t: t["priority"])):
            self.assertEqual(o["text"], p["text"])
            self.assertEqual(o["due"], p["due"])
            self.assertEqual(o["recur"], p["recur"])
            self.assertEqual(o["status"], p["status"])


class TestPriorityRepack(unittest.TestCase):
    """Priority repacking after mutations."""

    def setUp(self):
        self.tmp = _TMPDIR / "repack"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"

    def test_repack_after_completion(self):
        for name in ["A", "B", "C", "D"]:
            T.add_task(self.client, name)
        with patch.object(T, "display_tasks"):
            T.complete_task(self.client, 2)
        parsed = T.parse_task_file(self.client)
        open_tasks = sorted([t for t in parsed["tasks"] if not t["done"]],
                            key=lambda t: t["priority"])
        self.assertEqual([t["priority"] for t in open_tasks], [1, 2, 3])
        self.assertEqual([t["text"] for t in open_tasks], ["A", "C", "D"])

    def test_no_duplicate_priorities(self):
        for name in ["A", "B", "C"]:
            T.add_task(self.client, name)
        parsed = T.parse_task_file(self.client)
        priorities = [t["priority"] for t in parsed["tasks"] if not t["done"]]
        self.assertEqual(sorted(priorities), list(range(1, len(priorities) + 1)))


class TestAddTask(unittest.TestCase):
    """add_task behaviour."""

    def setUp(self):
        self.tmp = _TMPDIR / "add"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"

    def test_add_to_empty_client(self):
        T.add_task(self.client, "First task")
        data = T.parse_task_file(self.client)
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["text"], "First task")
        self.assertEqual(data["tasks"][0]["priority"], 1)

    def test_add_gets_next_priority(self):
        T.add_task(self.client, "Task A")
        T.add_task(self.client, "Task B")
        T.add_task(self.client, "Task C")
        data = T.parse_task_file(self.client)
        priorities = sorted(t["priority"] for t in data["tasks"])
        self.assertEqual(priorities, [1, 2, 3])

    def test_add_with_focus_creates_focus(self):
        T.add_task(self.client, "Platform task", focus="Platform")
        data = T.parse_task_file(self.client)
        self.assertIn("Platform", data["focuses"])

    def test_add_with_due_and_recur(self):
        T.add_task(self.client, "Weekly standup", due="02.06.2026", recur="monday")
        data = T.parse_task_file(self.client)
        t = data["tasks"][0]
        self.assertEqual(t["due"], "02.06.2026")
        self.assertEqual(t["recur"], "monday")

    def test_add_stamps_since(self):
        T.add_task(self.client, "Fresh task")
        data = T.parse_task_file(self.client)
        self.assertEqual(data["tasks"][0]["since"], datetime.now().strftime(T.DATE_FMT))

    def test_add_with_for(self):
        T.add_task(self.client, "Promised task", for_person="Sarah")
        data = T.parse_task_file(self.client)
        self.assertEqual(data["tasks"][0]["for"], "Sarah")


class TestCompleteTask(unittest.TestCase):
    """complete_task — archiving and recurrence."""

    def setUp(self):
        self.tmp = _TMPDIR / "complete"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"

    def test_complete_archives_task(self):
        T.add_task(self.client, "Task to complete")
        with patch.object(T, "display_tasks"):
            T.complete_task(self.client, 1)
        data = T.parse_task_file(self.client)
        open_tasks = [t for t in data["tasks"] if not t["done"]]
        self.assertEqual(len(open_tasks), 0)
        archive = T.archive_file(self.client)
        self.assertTrue(archive.exists())
        self.assertIn("Task to complete", archive.read_text())

    def test_complete_invalid_priority(self):
        T.add_task(self.client, "Only task")
        with patch.object(T, "display_tasks"):
            T.complete_task(self.client, 99)
        data = T.parse_task_file(self.client)
        self.assertEqual(len([t for t in data["tasks"] if not t["done"]]), 1)

    def test_recurring_task_reappears(self):
        T.add_task(self.client, "Weekly report", due="02.06.2026", recur="weekly")
        with patch.object(T, "display_tasks"):
            T.complete_task(self.client, 1)
        data = T.parse_task_file(self.client)
        open_tasks = [t for t in data["tasks"] if not t["done"]]
        self.assertEqual(len(open_tasks), 1)
        self.assertEqual(open_tasks[0]["text"], "Weekly report")
        self.assertEqual(open_tasks[0]["recur"], "weekly")
        self.assertEqual(open_tasks[0]["due"], "09.06.2026")

    def test_recurring_monthly_preserves_day(self):
        T.add_task(self.client, "Monthly review", due="15.05.2026", recur="monthly")
        with patch.object(T, "display_tasks"):
            T.complete_task(self.client, 1)
        data = T.parse_task_file(self.client)
        open_tasks = [t for t in data["tasks"] if not t["done"]]
        self.assertEqual(open_tasks[0]["due"], "15.06.2026")

    def test_complete_carries_for_to_archive(self):
        T.add_task(self.client, "Promised task", for_person="Sarah")
        with patch.object(T, "display_tasks"):
            T.complete_task(self.client, 1)
        archive = T.archive_file(self.client)
        self.assertIn("[for Sarah]", archive.read_text())

    def test_recurring_carries_for(self):
        T.add_task(self.client, "Weekly promise", due="02.06.2026", recur="weekly", for_person="Sarah")
        with patch.object(T, "display_tasks"):
            T.complete_task(self.client, 1)
        data = T.parse_task_file(self.client)
        open_tasks = [t for t in data["tasks"] if not t["done"]]
        self.assertEqual(open_tasks[0]["for"], "Sarah")

    def test_recurring_goes_to_bottom(self):
        T.add_task(self.client, "Task A")
        T.add_task(self.client, "Task B")
        T.add_task(self.client, "Weekly task", due="02.06.2026", recur="weekly")
        with patch.object(T, "display_tasks"):
            T.complete_task(self.client, 3)
        data = T.parse_task_file(self.client)
        open_tasks = sorted([t for t in data["tasks"] if not t["done"]],
                            key=lambda t: t["priority"])
        self.assertEqual(open_tasks[-1]["text"], "Weekly task")


class TestDeleteTask(unittest.TestCase):
    """delete_task — removes without marking done, archives as cancelled."""

    def setUp(self):
        self.tmp = _TMPDIR / "delete"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"

    def test_delete_removes_from_open_list(self):
        T.add_task(self.client, "Task to scrap")
        with patch.object(T, "display_tasks"):
            T.delete_task(self.client, 1)
        data = T.parse_task_file(self.client)
        self.assertEqual(len([t for t in data["tasks"] if not t["done"]]), 0)

    def test_delete_archives_as_cancelled_not_completed(self):
        T.add_task(self.client, "Task to scrap")
        with patch.object(T, "display_tasks"):
            T.delete_task(self.client, 1)
        content = T.archive_file(self.client).read_text()
        self.assertIn("- [-] Task to scrap", content)
        self.assertIn("[cancelled ", content)
        self.assertNotIn("[completed ", content)

    def test_delete_invalid_priority(self):
        T.add_task(self.client, "Only task")
        with patch.object(T, "display_tasks"):
            T.delete_task(self.client, 99)
        data = T.parse_task_file(self.client)
        self.assertEqual(len([t for t in data["tasks"] if not t["done"]]), 1)

    def test_delete_recurring_does_not_reschedule(self):
        T.add_task(self.client, "Weekly task", due="02.06.2026", recur="weekly")
        with patch.object(T, "display_tasks"):
            T.delete_task(self.client, 1)
        data = T.parse_task_file(self.client)
        self.assertEqual(len([t for t in data["tasks"] if not t["done"]]), 0)


class TestTaskMutations(unittest.TestCase):
    """edit, set_status, move, set_due_date, set_recur."""

    def setUp(self):
        self.tmp = _TMPDIR / "mutations"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"
        T.add_task(self.client, "Original text", focus="General", due="01.06.2026")
        T.add_task(self.client, "Second task", focus="General")

    def test_edit_task(self):
        T.edit_task(self.client, 1, "Updated text")
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertEqual(match["text"], "Updated text")

    def test_set_status_in_progress(self):
        T.set_task_status(self.client, 1, "in_progress")
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertEqual(match["status"], "in_progress")

    def test_set_status_blocked(self):
        T.set_task_status(self.client, 1, "blocked")
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertEqual(match["status"], "blocked")

    def test_set_status_reset(self):
        T.set_task_status(self.client, 1, "blocked")
        T.set_task_status(self.client, 1, "open")
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertEqual(match["status"], "open")

    def test_move_to_focus(self):
        T.move_task_to_focus(self.client, 1, "Platform")
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertEqual(match["focus"], "Platform")
        self.assertIn("Platform", data["focuses"])

    def test_set_due_date(self):
        T.set_due_date(self.client, 1, "30.06.2026")
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertEqual(match["due"], "30.06.2026")

    def test_clear_due_date(self):
        T.set_due_date(self.client, 1, None)
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertIsNone(match["due"])

    def test_set_recur(self):
        T.set_recur(self.client, 1, "weekly")
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertEqual(match["recur"], "weekly")

    def test_clear_recur(self):
        T.set_recur(self.client, 1, "weekly")
        T.set_recur(self.client, 1, None)
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertIsNone(match["recur"])

    def test_set_for(self):
        T.set_for(self.client, 1, "Sarah")
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertEqual(match["for"], "Sarah")

    def test_clear_for(self):
        T.set_for(self.client, 1, "Sarah")
        T.set_for(self.client, 1, None)
        data = T.parse_task_file(self.client)
        match = next(t for t in data["tasks"] if t["priority"] == 1)
        self.assertIsNone(match["for"])


class TestReprioritise(unittest.TestCase):
    """reprioritise_task."""

    def setUp(self):
        self.tmp = _TMPDIR / "repri"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"
        for name in ["A", "B", "C", "D"]:
            T.add_task(self.client, name)

    def test_move_last_to_first(self):
        with patch.object(T, "display_tasks"):
            T.reprioritise_task(self.client, 4, 1)
        data = T.parse_task_file(self.client)
        tasks = sorted([t for t in data["tasks"] if not t["done"]],
                       key=lambda t: t["priority"])
        self.assertEqual(tasks[0]["text"], "D")

    def test_move_first_to_last(self):
        with patch.object(T, "display_tasks"):
            T.reprioritise_task(self.client, 1, 4)
        data = T.parse_task_file(self.client)
        tasks = sorted([t for t in data["tasks"] if not t["done"]],
                       key=lambda t: t["priority"])
        self.assertEqual(tasks[-1]["text"], "A")

    def test_priorities_always_sequential(self):
        with patch.object(T, "display_tasks"):
            T.reprioritise_task(self.client, 2, 4)
        data = T.parse_task_file(self.client)
        priorities = sorted(t["priority"] for t in data["tasks"] if not t["done"])
        self.assertEqual(priorities, [1, 2, 3, 4])


class TestFocusOperations(unittest.TestCase):
    """create_focus, rename_focus, archive_focus."""

    def setUp(self):
        self.tmp = _TMPDIR / "focus"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"

    def test_create_focus(self):
        T.create_focus(self.client, "Platform")
        data = T.parse_task_file(self.client)
        self.assertIn("Platform", data["focuses"])

    def test_create_focus_with_description(self):
        T.create_focus(self.client, "Platform", "Supporting the platform team")
        data = T.parse_task_file(self.client)
        self.assertEqual(data["descriptions"]["Platform"], "Supporting the platform team")

    def test_create_duplicate_focus_ignored(self):
        T.create_focus(self.client, "Platform")
        T.create_focus(self.client, "Platform")
        data = T.parse_task_file(self.client)
        self.assertEqual(data["focuses"].count("Platform"), 1)

    def test_rename_focus(self):
        T.create_focus(self.client, "Old Name")
        T.add_task(self.client, "A task", focus="Old Name")
        T.rename_focus(self.client, "Old Name", "New Name")
        data = T.parse_task_file(self.client)
        self.assertIn("New Name", data["focuses"])
        self.assertNotIn("Old Name", data["focuses"])
        self.assertEqual(data["tasks"][0]["focus"], "New Name")

    def test_archive_focus_moves_tasks(self):
        T.create_focus(self.client, "Old Focus")
        T.add_task(self.client, "Task in focus", focus="Old Focus")
        T.archive_focus(self.client, "Old Focus")
        data = T.parse_task_file(self.client)
        open_tasks = [t for t in data["tasks"] if not t["done"]]
        self.assertEqual(len(open_tasks), 0)
        archive = T.archive_file(self.client)
        self.assertIn("Task in focus", archive.read_text())


class TestListClients(unittest.TestCase):
    """list_clients only returns non-archive files."""

    def setUp(self):
        self.tmp = _TMPDIR / "clients"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)

    def test_list_clients_excludes_archive(self):
        T.add_task("acme_lc", "Task A")
        T.add_task("widgets_lc", "Task B")
        T.archive_file("acme_lc").write_text("# Archive\n")
        clients = T.list_clients()
        self.assertIn("acme_lc", clients)
        self.assertIn("widgets_lc", clients)
        self.assertNotIn("acme_lc_archive", clients)


class TestMockedInference(unittest.TestCase):
    """run_inference with mocked API — verifies operation routing."""

    def setUp(self):
        self.tmp = _TMPDIR / "inference"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"
        T.add_task(self.client, "Existing task")

    def _mock_response(self, ops: list) -> MagicMock:
        import json
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"text": json.dumps(ops)}]
        }
        return mock_resp

    def test_add_via_inference(self):
        ops = [{"action": "add", "text": "New task", "focus": "General",
                "due": None, "recur": None, "priority": 2}]
        with patch("requests.post", return_value=self._mock_response(ops)):
            with patch.object(T, "is_online", return_value=True):
                T.force_local = False
                result = T.run_inference(self.client, "Add a task called New task")
        self.assertEqual(result[0]["action"], "add")
        self.assertEqual(result[0]["text"], "New task")

    def test_complete_via_inference(self):
        ops = [{"action": "complete", "priority": 1}]
        with patch("requests.post", return_value=self._mock_response(ops)):
            with patch.object(T, "is_online", return_value=True):
                with patch.object(T, "display_tasks"):
                    T.force_local = False
                    result = T.run_inference(self.client, "Task 1 is done")
        self.assertEqual(result[0]["action"], "complete")

    def test_delete_action_via_inference(self):
        ops = [{"action": "delete", "priority": 1}]
        with patch("requests.post", return_value=self._mock_response(ops)):
            with patch.object(T, "is_online", return_value=True):
                with patch.object(T, "display_tasks"):
                    T.force_local = False
                    result = T.run_inference(self.client, "never mind task 1, scrap it")
        self.assertEqual(result[0]["action"], "delete")

    def test_for_action_via_inference(self):
        ops = [{"action": "for", "priority": 1, "for": "Sarah"}]
        with patch("requests.post", return_value=self._mock_response(ops)):
            with patch.object(T, "is_online", return_value=True):
                T.force_local = False
                result = T.run_inference(self.client, "Task 1 is for Sarah")
        self.assertEqual(result[0]["action"], "for")
        self.assertEqual(result[0]["for"], "Sarah")

    def test_offline_falls_back_to_ollama(self):
        ollama_resp = MagicMock()
        ollama_resp.ok = True
        ollama_resp.raise_for_status = MagicMock()
        ollama_resp.json.return_value = {
            "message": {"content": '[{"action": "none", "response": "OK"}]'}
        }
        with patch("requests.post", return_value=ollama_resp):
            with patch.object(T, "is_online", return_value=False):
                T.force_local = False
                result = T.run_inference(self.client, "hello")
        self.assertEqual(result[0]["action"], "none")


class TestPhotoTask(unittest.TestCase):
    """photo_task.py — mocked vision API."""

    def setUp(self):
        self.tmp = _TMPDIR / "photo"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"
        self.fake_image = self.tmp / "screenshot.png"
        self.fake_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    def _mock_vision_response(self, tasks: list, summary: str) -> MagicMock:
        import json
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"text": json.dumps({"tasks": tasks, "summary": summary})}]
        }
        return mock_resp

    def test_extracts_tasks_from_image(self):
        import photo_task as PT
        import tasks_core as C
        C.init(self.tmp)
        PT.BASE_DIR = self.tmp

        ops = [{"action": "add", "text": "Follow up with James",
                "focus": "General", "due": None, "recur": None, "priority": 1}]
        mock_resp = self._mock_vision_response(ops, f"{self.client}: Added 1 task — Follow up with James")

        with patch("requests.post", return_value=mock_resp):
            summary = PT.extract_tasks_from_image(self.client, self.fake_image)

        self.assertIn("Follow up with James", summary)
        data = T.parse_task_file(self.client)
        self.assertEqual(len([t for t in data["tasks"] if not t["done"]]), 1)
        self.assertEqual(data["tasks"][0]["text"], "Follow up with James")

    def test_no_tasks_returns_summary(self):
        import photo_task as PT
        import tasks_core as C
        C.init(self.tmp)
        PT.BASE_DIR = self.tmp

        mock_resp = self._mock_vision_response([], f"{self.client}: No tasks found in image")

        with patch("requests.post", return_value=mock_resp):
            summary = PT.extract_tasks_from_image(self.client, self.fake_image)

        self.assertIn("No tasks found", summary)
        data = T.parse_task_file(self.client)
        self.assertEqual(len(data["tasks"]), 0)


class TestReport(unittest.TestCase):
    """generate_report — completed/in-progress/blocked summary."""

    def setUp(self):
        self.tmp = _TMPDIR / "report"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"

    def _archive_with_completed_date(self, text: str, completed: str, for_person=None):
        for_str = f" [for {for_person}]" if for_person else ""
        line = f"- [x] {text}{for_str} [completed {completed}]\n"
        arc = T.archive_file(self.client)
        if not arc.exists():
            arc.write_text(f"# {self.client.title()} — Archive\n\n")
        with arc.open("a") as f:
            f.write(line)

    def test_includes_recently_completed(self):
        today = datetime.now()
        recent = (today - timedelta(days=2)).strftime(T.DATE_FMT)
        self._archive_with_completed_date("Recent task", recent)
        report = T.generate_report(self.client, days=7)
        self.assertIn("Recent task", report)

    def test_excludes_old_completed(self):
        today = datetime.now()
        old = (today - timedelta(days=30)).strftime(T.DATE_FMT)
        self._archive_with_completed_date("Old task", old)
        report = T.generate_report(self.client, days=7)
        self.assertNotIn("Old task", report)

    def test_excludes_cancelled_tasks(self):
        T.add_task(self.client, "Scrapped task")
        with patch.object(T, "display_tasks"):
            T.delete_task(self.client, 1)
        report = T.generate_report(self.client, days=7)
        self.assertNotIn("Scrapped task", report)

    def test_includes_for_annotation(self):
        today = datetime.now()
        recent = (today - timedelta(days=1)).strftime(T.DATE_FMT)
        self._archive_with_completed_date("Promised task", recent, for_person="Sarah")
        report = T.generate_report(self.client, days=7)
        self.assertIn("Promised task (for Sarah)", report)

    def test_includes_in_progress_and_blocked(self):
        T.add_task(self.client, "In flight")
        T.set_task_status(self.client, 1, "in_progress")
        T.add_task(self.client, "Stuck")
        T.set_task_status(self.client, 2, "blocked")
        report = T.generate_report(self.client, days=7)
        self.assertIn("## In progress", report)
        self.assertIn("In flight", report)
        self.assertIn("## Blocked", report)
        self.assertIn("Stuck", report)

    def test_save_report_writes_file(self):
        report = T.generate_report(self.client, days=7)
        path = T.save_report(self.client, report)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(), report)


class TestReview(unittest.TestCase):
    """build_review_queue — overdue/stale/blocked/empty-focus classification."""

    def setUp(self):
        self.tmp = _TMPDIR / "review"
        self.tmp.mkdir(exist_ok=True)
        _set_tmp_dirs(self.tmp)
        self.client = f"test_{self._testMethodName}"

    def test_overdue_task_included(self):
        T.add_task(self.client, "Late task", due="01.01.2020")
        queue = T.build_review_queue(self.client)
        self.assertEqual(len(queue["overdue"]), 1)
        self.assertEqual(queue["overdue"][0]["text"], "Late task")

    def test_future_due_not_overdue(self):
        future = (datetime.now() + timedelta(days=30)).strftime(T.DATE_FMT)
        T.add_task(self.client, "Future task", due=future)
        queue = T.build_review_queue(self.client)
        self.assertEqual(len(queue["overdue"]), 0)

    def test_stale_task_no_due_old_since(self):
        T.add_task(self.client, "Old undated task")
        data = T.parse_task_file(self.client)
        old_since = (datetime.now() - timedelta(days=30)).strftime(T.DATE_FMT)
        data["tasks"][0]["since"] = old_since
        T.write_task_file(self.client, data)
        queue = T.build_review_queue(self.client)
        self.assertEqual(len(queue["stale"]), 1)

    def test_recent_undated_task_not_stale(self):
        T.add_task(self.client, "New undated task")
        queue = T.build_review_queue(self.client)
        self.assertEqual(len(queue["stale"]), 0)

    def test_missing_since_treated_as_stale(self):
        T.add_task(self.client, "Legacy task")
        data = T.parse_task_file(self.client)
        data["tasks"][0]["since"] = None
        T.write_task_file(self.client, data)
        queue = T.build_review_queue(self.client)
        self.assertEqual(len(queue["stale"]), 1)

    def test_blocked_task_included(self):
        T.add_task(self.client, "Stuck task")
        T.set_task_status(self.client, 1, "blocked")
        queue = T.build_review_queue(self.client)
        self.assertEqual(len(queue["blocked"]), 1)

    def test_empty_focus_included(self):
        T.create_focus(self.client, "Empty Focus")
        queue = T.build_review_queue(self.client)
        self.assertIn("Empty Focus", queue["empty_focuses"])

    def test_focus_with_task_not_empty(self):
        T.add_task(self.client, "A task", focus="Platform")
        queue = T.build_review_queue(self.client)
        self.assertNotIn("Platform", queue["empty_focuses"])


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Suppress Rich console output during tests
    T.console = MagicMock()
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
