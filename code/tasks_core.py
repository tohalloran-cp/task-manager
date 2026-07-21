#!/usr/bin/env python3
"""
tasks_core.py — Shared core for task file operations.
Imported by tasks.py, photo_task.py, and voice_dump.py.

Call init(base_dir) before using any other function.

Version history:
    1.0  Initial release — extracted from tasks.py
         init(base_dir) configures all paths
         parse_task_file, write_task_file, archive_task
         client_file, archive_file, list_clients
         load_last_client, save_last_client
         next_recur_date
    1.1  Added [for Name] (who a task was promised to) and [since DATE]
         (creation date) task tags — parsed, written, and carried into
         the archive line on completion (for only, not since)
         Added parse_archive_file() to read completed-task history
"""

VERSION = "1.1"

import re
import calendar
from datetime import datetime, timedelta
from pathlib import Path

# ── Module-level path state (set by init) ─────────────────────────────────────

BASE_DIR  = None
TASKS_DIR = None
DATE_FMT  = "%d.%m.%Y"


def init(base_dir: Path):
    """Configure base directory. Must be called before any other function."""
    global BASE_DIR, TASKS_DIR
    BASE_DIR  = Path(base_dir)
    TASKS_DIR = BASE_DIR / "tasks"
    TASKS_DIR.mkdir(parents=True, exist_ok=True)


def _require_init():
    if BASE_DIR is None:
        raise RuntimeError("tasks_core.init() has not been called")


# ── File helpers ──────────────────────────────────────────────────────────────

def client_file(client: str) -> Path:
    _require_init()
    return TASKS_DIR / f"{client}.md"


def archive_file(client: str) -> Path:
    _require_init()
    return TASKS_DIR / f"{client}_archive.md"


def list_clients() -> list[str]:
    _require_init()
    return sorted(
        p.stem for p in TASKS_DIR.glob("*.md")
        if not p.stem.endswith("_archive")
    )


def save_last_client(client: str | None):
    _require_init()
    path = BASE_DIR / ".last_task_client"
    if client:
        path.write_text(client)
    elif path.exists():
        path.unlink()


def load_last_client() -> str | None:
    _require_init()
    path = BASE_DIR / ".last_task_client"
    if path.exists():
        name = path.read_text().strip()
        if name and client_file(name).exists():
            return name
    return None


# ── Markdown parsing ──────────────────────────────────────────────────────────

def parse_task_file(client: str) -> dict:
    """
    Parse client task file into:
    {
        "focuses": [...],
        "descriptions": {"Focus": "description", ...},
        "tasks": [{"priority", "focus", "text", "due", "recur", "done", "status"}, ...]
    }
    """
    _require_init()
    path = client_file(client)
    if not path.exists():
        return {"focuses": ["General"], "descriptions": {}, "tasks": []}

    focuses = []
    descriptions = {}
    tasks = []
    current_focus = None

    for line in path.read_text().splitlines():
        if line.startswith("## "):
            current_focus = line[3:].strip()
            if current_focus not in focuses:
                focuses.append(current_focus)
        elif line.startswith("<!-- ") and line.endswith(" -->") and current_focus:
            descriptions[current_focus] = line[5:-4].strip()
        elif re.match(r"^- \[[ x~!]\]", line) and current_focus:
            marker = line[3]
            done = marker == "x"
            status = {"x": "done", "~": "in_progress", "!": "blocked"}.get(marker, "open")
            rest = line[6:].strip()
            pri_match = re.match(r"#(\d+)\s+", rest)
            priority = int(pri_match.group(1)) if pri_match else 999
            rest = rest[pri_match.end():] if pri_match else rest
            due_match = re.search(r"\[due ([\d.]+)\]", rest)
            due = due_match.group(1) if due_match else None
            recur_match = re.search(r"\[every ([^\]]+)\]", rest)
            recur = recur_match.group(1).strip() if recur_match else None
            for_match = re.search(r"\[for ([^\]]+)\]", rest)
            for_person = for_match.group(1).strip() if for_match else None
            since_match = re.search(r"\[since ([\d.]+)\]", rest)
            since = since_match.group(1) if since_match else None
            text = re.sub(r"\s*\[due [\d.]+\]", "", rest)
            text = re.sub(r"\s*\[every [^\]]+\]", "", text)
            text = re.sub(r"\s*\[for [^\]]+\]", "", text)
            text = re.sub(r"\s*\[since [\d.]+\]", "", text).strip()
            tasks.append({
                "priority": priority,
                "focus": current_focus,
                "text": text,
                "due": due,
                "recur": recur,
                "for": for_person,
                "since": since,
                "done": done,
                "status": status,
            })

    return {"focuses": focuses, "descriptions": descriptions, "tasks": tasks}


def write_task_file(client: str, data: dict):
    """Write task structure back to markdown, repacking all non-done priorities."""
    _require_init()
    MARKERS = {"open": " ", "in_progress": "~", "blocked": "!", "done": "x"}

    open_tasks = [t for t in data["tasks"] if not t["done"]]
    open_tasks.sort(key=lambda t: t["priority"])
    for i, task in enumerate(open_tasks, 1):
        task["priority"] = i

    lines = [f"# {client.replace('_', ' ').title()}", ""]

    focuses = data["focuses"]
    if "General" not in focuses:
        focuses = ["General"] + focuses
    elif focuses[0] != "General":
        focuses = ["General"] + [f for f in focuses if f != "General"]

    for focus in focuses:
        focus_tasks = [t for t in open_tasks if t["focus"] == focus]
        lines.append(f"## {focus}")
        desc = data.get("descriptions", {}).get(focus)
        if desc:
            lines.append(f"<!-- {desc} -->")
        for task in focus_tasks:
            marker = MARKERS.get(task.get("status", "open"), " ")
            due_str = f" [due {task['due']}]" if task.get("due") else ""
            recur_str = f" [every {task['recur']}]" if task.get("recur") else ""
            for_str = f" [for {task['for']}]" if task.get("for") else ""
            since_str = f" [since {task['since']}]" if task.get("since") else ""
            lines.append(f"- [{marker}] #{task['priority']} {task['text']}{due_str}{recur_str}{for_str}{since_str}")
        lines.append("")

    client_file(client).write_text("\n".join(lines))


def archive_task(client: str, task: dict):
    """Append a completed task to the archive file."""
    _require_init()
    arc = archive_file(client)
    if not arc.exists():
        arc.write_text(f"# {client.replace('_', ' ').title()} — Archive\n\n")
    today = datetime.now().strftime(DATE_FMT)
    due_str = f" [due {task['due']}]" if task.get("due") else ""
    for_str = f" [for {task['for']}]" if task.get("for") else ""
    line = f"- [x] {task['text']}{due_str}{for_str} [completed {today}]\n"
    with arc.open("a") as f:
        f.write(line)


def parse_archive_file(client: str) -> list[dict]:
    """Parse a client's archive file into a list of completed tasks."""
    _require_init()
    arc = archive_file(client)
    if not arc.exists():
        return []

    pattern = re.compile(
        r"^- \[x\] (.+?)(?: \[due ([\d.]+)\])?(?: \[for ([^\]]+)\])? \[completed ([\d.]+)\]$"
    )
    entries = []
    for line in arc.read_text().splitlines():
        match = pattern.match(line)
        if match:
            text, due, for_person, completed = match.groups()
            entries.append({
                "text": text.strip(),
                "due": due,
                "for": for_person,
                "completed": completed,
            })
    return entries


# ── Recurrence ────────────────────────────────────────────────────────────────

def next_recur_date(recur: str, from_date: datetime, due_date: datetime | None = None) -> str | None:
    """
    Calculate next due date for a recurring task.
    Uses due_date as anchor for month-based recurrence to avoid drift.
    Supports numeric prefixes: '3 months', '2 weeks', 'every year'.
    """
    recur = recur.lower().strip()
    anchor = due_date or from_date
    DAYS = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    num_match = re.match(r"^(\d+)\s+(.+)$", recur)
    if num_match:
        n = int(num_match.group(1))
        unit = num_match.group(2).rstrip("s")
    else:
        n = 1
        unit = recur.rstrip("s")

    if unit in ("day", "daily"):
        return (anchor + timedelta(days=n)).strftime(DATE_FMT)
    elif unit in ("week", "weekly"):
        return (anchor + timedelta(weeks=n)).strftime(DATE_FMT)
    elif unit in ("fortnight", "fortnightly"):
        return (anchor + timedelta(weeks=2 * n)).strftime(DATE_FMT)
    elif unit in ("month", "monthly"):
        total = anchor.month + n
        year = anchor.year + (total - 1) // 12
        month = ((total - 1) % 12) + 1
        try:
            return anchor.replace(year=year, month=month).strftime(DATE_FMT)
        except ValueError:
            last_day = calendar.monthrange(year, month)[1]
            return anchor.replace(year=year, month=month, day=last_day).strftime(DATE_FMT)
    elif unit in ("year", "yearly", "annual", "annually"):
        try:
            return anchor.replace(year=anchor.year + n).strftime(DATE_FMT)
        except ValueError:
            return anchor.replace(year=anchor.year + n, day=28).strftime(DATE_FMT)
    elif unit in DAYS:
        target = DAYS[unit]
        days_ahead = (target - anchor.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (anchor + timedelta(days=days_ahead * n)).strftime(DATE_FMT)

    return None
