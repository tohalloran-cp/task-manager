#!/usr/bin/env python3
"""
tasks.py — Task management for consultants across multiple clients.
Uses Anthropic API for natural language task operations.
Falls back to local Ollama when offline.

Version history:
    1.0  Initial release — standalone task manager with Anthropic API
         Natural language inference + explicit commands
         Per-client markdown files with focus sections
         Global priority numbering, auto-repack on completion
         Archive to separate file on completion
         Voice capture via whisper.cpp
         Offline fallback to local Ollama
    1.1  Fixed Anthropic model string to claude-sonnet-4-6
    1.2  Added session logging to logs/tasks_YYYYMMDD_HHMMSS.log
         Added /task add explicit command with focus: and due: params
    1.3  Focus descriptions — prompt on /focus new, stored as HTML comments
         Descriptions shown in /focus list and included in inference prompt
    1.4  Added in-progress [~] and blocked [!] task statuses
         /task start, /task block, /task reset commands
         Status colours in task list, blocked shown in /status
         Model can infer start/block/reset from natural language
    1.5  Fixed NameError in display_status — focuses_with_tasks was displaced
    1.6  Fixed merged set_task_status and reprioritise_task functions
    1.7  Fixed priority repack — all non-done tasks repacked together, no clashes
         Fixed inference JSON parsing — strips thinking tags, finds array anywhere in response
    1.8  Fixed is_online() — was hitting api.anthropic.com root which returns 404
         Now checks google.com for connectivity instead
    1.9  Added detailed API error output to diagnose 404 issues
    2.0  Used full paths for ffmpeg and termux-microphone-record
         Auto-delete old voice files before starting new recording
    2.1  Moved voice temp files to Termux /tmp to avoid permission issues
    2.2  Added /local toggle to force local Ollama regardless of connectivity
    2.3  /voice is now a toggle — tap once to start, tap again to stop and process
    2.4  Added task editing — /task edit <#> and natural language inference
    2.5  Fixed JSON parsing — bracket-counting to find exact array end, handles extra data
    2.6  Due dates shown in cyan in task list, /task due <#> clear removes due date
    2.7  Fixed due date colour to cyan — was clashing with in-progress yellow
    2.8  Added /list shortcut for /task list
    2.9  Auto-display task list after completing or reprioritising a task
    3.0  Switched whisper model to small.en for better transcription quality
    3.1  Improved recording quality — 128kbps bitrate, whisper prompt for consultant context
    3.2  Switched whisper model to medium.en for best English transcription quality
    4.1  Added Notability to supported screenshot apps alongside OneNote
         tasks.py, photo_task.py, voice_dump.py all import from tasks_core
         No more duplicated parse/write/archive code
    4.2  Removed duplicate add_task() definition (identical copy, dead code)
    4.3  Commitment tracking — [for Name] task tag for who a task was promised
         to, plus automatic [since DATE] creation stamp on every new task.
         /task for <#> <name>, /task for <#> clear, "for" inference action.
         Shown in task list and folded into the Commitments status section.
    4.4  Client status reports — /report [days] generates a plain-text
         completed/in-progress/blocked summary from the archive + open tasks,
         printed and saved to reports/<client>_<date>.md
    4.5  Weekly review — /review walks overdue, stale (>14d open, no due date),
         and blocked tasks for the current client one at a time with inline
         triage actions (done/start/block/reset/update due/keep/quit)
    4.6  Added delete_task() — /task delete <#> (alias /task cancel), a
         "delete" inference action, and an [x]delete option in /review.
         Removes a task from the open list without marking it done; archived
         with a [cancelled DATE] tag instead of [completed DATE] so it's
         never mistaken for finished work in /report. Required updating
         generate_report() for tasks_core 1.2's renamed archive fields
         (parse_archive_file now returns "status"/"date", not "completed").
    4.7  Fixed archive_task(c, t) convenience alias — never updated to accept
         the status param added in 4.6, so delete_task() crashed with
         "TypeError: archive_task() got an unexpected keyword argument
         'status'" every time it ran. test_tasks.py's _set_tmp_dirs() had
         been monkey-patching this exact alias to the real tasks_core
         function during tests, silently bypassing the broken wrapper —
         removed that reassignment so tests exercise the real one.

Requirements:
    pip install requests rich prompt_toolkit

Usage:
    python tasks.py
"""

VERSION = "4.7"

import os
import re
import sys
import json
import logging
import subprocess
import requests
from datetime import datetime, timedelta
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
import tasks_core as core

# ── Configuration ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = "claude-sonnet-4-6"
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"

OLLAMA_URL        = "http://localhost:11434/api/chat"
OLLAMA_MODEL      = "qwen3:1.7b"

BASE_DIR          = Path("/storage/emulated/0/Documents/ai-assistant")
LOG_DIR           = BASE_DIR / "logs"
WHISPER_BIN       = Path("/data/data/com.termux/files/home/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL     = Path("/data/data/com.termux/files/home/whisper.cpp/models/ggml-medium.en.bin")
VOICE_RAW         = Path("/data/data/com.termux/files/usr/tmp/voice_raw.wav")
VOICE_16K         = Path("/data/data/com.termux/files/usr/tmp/voice_16k.wav")

DATE_FMT          = core.DATE_FMT

# Convenience aliases so existing code doesn't need to change
def client_file(client):    return core.client_file(client)
def archive_file(client):   return core.archive_file(client)
def list_clients():         return core.list_clients()
def save_last_client(c):    return core.save_last_client(c)
def load_last_client():     return core.load_last_client()
def parse_task_file(c):     return core.parse_task_file(c)
def write_task_file(c, d):  return core.write_task_file(c, d)
def archive_task(c, t, status="completed"): return core.archive_task(c, t, status)
def next_recur_date(*a, **k): return core.next_recur_date(*a, **k)

console = Console()
voice_enabled = False
force_local = False  # toggled with /local

# ── Logging ───────────────────────────────────────────────────────────────────

_logger = None

def init_logger():
    global _logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"tasks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        filename=str(log_file),
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S"
    )
    _logger = logging.getLogger("tasks")
    _logger.info(f"Session started — v{VERSION}, model {ANTHROPIC_MODEL}")
    return log_file

def log(level: str, msg: str):
    if _logger:
        getattr(_logger, level, _logger.info)(msg)


# ── Display helpers ───────────────────────────────────────────────────────────

def display_tasks(client: str, focus_filter: str | None = None):
    """Display open tasks sorted by priority."""
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]

    if focus_filter:
        # Fuzzy match focus name
        matches = [f for f in data["focuses"] if focus_filter.lower() in f.lower()]
        if matches:
            open_tasks = [t for t in open_tasks if t["focus"] in matches]
        else:
            console.print(f"[yellow]No focus matching '{focus_filter}'[/yellow]")
            return

    if not open_tasks:
        console.print("[dim]No open tasks.[/dim]")
        return

    open_tasks.sort(key=lambda t: t["priority"])

    # Group by focus for display
    by_focus = {}
    for task in open_tasks:
        by_focus.setdefault(task["focus"], []).append(task)

    lines = []
    STATUS_COLOUR = {"open": "white", "in_progress": "yellow", "blocked": "red"}
    STATUS_LABEL  = {"in_progress": " [dim][~][/dim]", "blocked": " [dim][!][/dim]"}
    today = datetime.now()

    def due_display(t: dict) -> str:
        if not t.get("due"):
            return ""
        try:
            due_date = datetime.strptime(t["due"], DATE_FMT)
            days = (due_date - today).days
            if days < 0:
                return f" [bold red][OVERDUE {t['due']}][/bold red]"
            elif days == 0:
                return f" [bold yellow][due TODAY][/bold yellow]"
            elif days <= 3:
                return f" [orange1][due {t['due']}][/orange1]"
            elif days <= 7:
                return f" [yellow][due {t['due']}][/yellow]"
            else:
                return f" [cyan][due {t['due']}][/cyan]"
        except ValueError:
            return f" [cyan][due {t['due']}][/cyan]"

    for focus, tasks in by_focus.items():
        lines.append(f"[bold]{focus}[/bold]")
        for t in tasks:
            status = t.get("status", "open")
            colour = STATUS_COLOUR.get(status, "white")
            label = STATUS_LABEL.get(status, "")
            recur = f" [dim][↻ {t['recur']}][/dim]" if t.get("recur") else ""
            for_tag = f" [magenta][for {t['for']}][/magenta]" if t.get("for") else ""
            lines.append(f"  [{colour}]#{t['priority']} {t['text']}[/{colour}]{label}{due_display(t)}{recur}{for_tag}")
        lines.append("")

    console.print(Panel("\n".join(lines).strip(), title=f"{client} — Tasks", border_style="blue"))


def display_status(client: str):
    """Show top priorities, due soon, and empty focuses."""
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    open_tasks.sort(key=lambda t: t["priority"])

    today = datetime.now()
    lines = []

    # Top 3
    lines.append("[bold]Top priorities[/bold]")
    for t in open_tasks[:3]:
        due = f" [due {t['due']}]" if t.get("due") else ""
        lines.append(f"  [cyan]#{t['priority']}[/cyan] [{t['focus']}] {t['text']}{due}")

    # Commitments — tasks with a due date and/or promised to someone
    commitments = [t for t in open_tasks if t.get("due") or t.get("for")]
    if commitments:
        dated = [t for t in commitments if t.get("due")]
        undated = [t for t in commitments if not t.get("due")]
        dated.sort(key=lambda t: datetime.strptime(t["due"], DATE_FMT) if t.get("due") else datetime.max)
        undated.sort(key=lambda t: t.get("for") or "")
        lines.append("\n[bold]Commitments[/bold]")
        for t in dated:
            recur = f" [dim][↻ {t['recur']}][/dim]" if t.get("recur") else ""
            for_tag = f" [magenta](for {t['for']})[/magenta]" if t.get("for") else ""
            try:
                due_date = datetime.strptime(t["due"], DATE_FMT)
                days = (due_date - today).days
                if days < 0:
                    marker = f"[bold red]OVERDUE {abs(days)}d[/bold red]"
                elif days == 0:
                    marker = "[bold yellow]TODAY[/bold yellow]"
                elif days <= 3:
                    marker = f"[orange1]in {days}d[/orange1]"
                elif days <= 7:
                    marker = f"[yellow]in {days}d[/yellow]"
                else:
                    marker = f"[cyan]{t['due']}[/cyan]"
                lines.append(f"  [dim]#{t['priority']}[/dim] {t['text']} {marker}{recur}{for_tag}")
            except ValueError:
                lines.append(f"  [dim]#{t['priority']}[/dim] {t['text']} [cyan]{t['due']}[/cyan]{recur}{for_tag}")
        for t in undated:
            lines.append(f"  [dim]#{t['priority']}[/dim] {t['text']} [magenta]for {t['for']}[/magenta]")

    # Blocked tasks
    blocked = [t for t in open_tasks if t.get("status") == "blocked"]
    if blocked:
        lines.append("\n[bold red]Blocked[/bold red]")
        for t in blocked:
            lines.append(f"  [red]#{t['priority']}[/red] {t['text']}")

    # Empty focuses
    focuses_with_tasks = {t["focus"] for t in open_tasks}
    empty = [f for f in data["focuses"] if f not in focuses_with_tasks]
    if empty:
        lines.append(f"\n[bold]Focuses with no tasks[/bold]\n  " + ", ".join(empty))

    console.print(Panel("\n".join(lines), title=f"{client} — Status", border_style="green"))


# ── Direct task operations ────────────────────────────────────────────────────

def add_task(client: str, text: str, focus: str = "General", due: str | None = None,
             recur: str | None = None, for_person: str | None = None):
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    next_pri = max((t["priority"] for t in open_tasks), default=0) + 1

    if focus not in data["focuses"]:
        data["focuses"].append(focus)

    data["tasks"].append({
        "priority": next_pri,
        "focus": focus,
        "text": text,
        "due": due,
        "recur": recur,
        "for": for_person,
        "since": datetime.now().strftime(DATE_FMT),
        "done": False,
        "status": "open",
    })
    write_task_file(client, data)
    recur_str = f" [↻ {recur}]" if recur else ""
    for_str = f" [for {for_person}]" if for_person else ""
    log("info", f"Task added: [{focus}] #{next_pri} {text}{recur_str}{for_str}")
    console.print(f"[green]✓ Added #{next_pri}: {text}{recur_str}{for_str}[/green]")


def complete_task(client: str, priority: int):
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return

    match["done"] = True
    archive_task(client, match)
    log("info", f"Task completed: #{priority} {match['text']}")
    console.print(f"[green]✓ Completed: {match['text']}[/green]")

    # If recurring, add it back with next due date
    if match.get("recur"):
        anchor = None
        if match.get("due"):
            try:
                anchor = datetime.strptime(match["due"], DATE_FMT)
            except ValueError:
                pass
        next_due = next_recur_date(match["recur"], datetime.now(), anchor)
        next_pri = max((t["priority"] for t in open_tasks if not t["done"] and t != match), default=0) + 1
        new_task = {
            "priority": next_pri,
            "focus": match["focus"],
            "text": match["text"],
            "due": next_due,
            "recur": match["recur"],
            "for": match.get("for"),
            "since": datetime.now().strftime(DATE_FMT),
            "done": False,
            "status": "open",
        }
        data["tasks"].append(new_task)
        console.print(f"[dim]↻ Recurring — next due {next_due}[/dim]")

    write_task_file(client, data)
    display_tasks(client)


def delete_task(client: str, priority: int):
    """Remove an open task without marking it done. Archived as cancelled, not completed."""
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return

    match["done"] = True
    archive_task(client, match, status="cancelled")
    log("info", f"Task cancelled: #{priority} {match['text']}")
    console.print(f"[yellow]✗ Cancelled: {match['text']}[/yellow]")

    write_task_file(client, data)
    display_tasks(client)


def set_task_status(client: str, priority: int, status: str):
    """Set status of an open task: open, in_progress, blocked."""
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return
    old_status = match.get("status", "open")
    match["status"] = status
    write_task_file(client, data)
    labels = {"open": "open", "in_progress": "in progress", "blocked": "blocked"}
    log("info", f"Task #{priority} status: {old_status} → {status}")
    console.print(f"[green]✓ #{priority} marked {labels[status]}[/green]")


def reprioritise_task(client: str, priority: int, new_priority: int):
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    open_tasks.sort(key=lambda t: t["priority"])
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return
    open_tasks.remove(match)
    new_priority = max(1, min(new_priority, len(open_tasks) + 1))
    open_tasks.insert(new_priority - 1, match)
    for i, t in enumerate(open_tasks, 1):
        t["priority"] = i
    write_task_file(client, data)
    console.print(f"[green]✓ Task moved to #{new_priority}[/green]")
    display_tasks(client)


def move_task_to_focus(client: str, priority: int, focus: str):
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return
    if focus not in data["focuses"]:
        data["focuses"].append(focus)
    old_focus = match["focus"]
    match["focus"] = focus
    write_task_file(client, data)
    console.print(f"[green]✓ Moved #{priority} from {old_focus} to {focus}[/green]")


def set_due_date(client: str, priority: int, due: str | None):
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return
    match["due"] = due
    write_task_file(client, data)
    label = due if due else "cleared"
    console.print(f"[green]✓ Due date {label} for #{priority}[/green]")


def set_recur(client: str, priority: int, recur: str | None):
    """Set or clear recurrence on a task."""
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return
    match["recur"] = recur
    write_task_file(client, data)
    label = f"every {recur}" if recur else "cleared"
    console.print(f"[green]✓ Recurrence {label} for #{priority}[/green]")


def set_for(client: str, priority: int, for_person: str | None):
    """Set or clear who a task was promised to."""
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return
    match["for"] = for_person
    write_task_file(client, data)
    label = f"for {for_person}" if for_person else "cleared"
    console.print(f"[green]✓ #{priority} {label}[/green]")


def create_focus(client: str, focus: str, description: str = ""):
    data = parse_task_file(client)
    if focus in data["focuses"]:
        console.print(f"[yellow]Focus '{focus}' already exists[/yellow]")
        return
    data["focuses"].append(focus)
    if description:
        data.setdefault("descriptions", {})[focus] = description
    write_task_file(client, data)
    console.print(f"[green]✓ Focus '{focus}' created[/green]")
    if description:
        console.print(f"[dim]  {description}[/dim]")


def rename_focus(client: str, old: str, new: str):
    data = parse_task_file(client)
    if old not in data["focuses"]:
        console.print(f"[yellow]Focus '{old}' not found[/yellow]")
        return
    data["focuses"] = [new if f == old else f for f in data["focuses"]]
    for t in data["tasks"]:
        if t["focus"] == old:
            t["focus"] = new
    write_task_file(client, data)
    console.print(f"[green]✓ Focus renamed to '{new}'[/green]")


def archive_focus(client: str, focus: str):
    data = parse_task_file(client)
    tasks_in_focus = [t for t in data["tasks"] if t["focus"] == focus and not t["done"]]
    for t in tasks_in_focus:
        t["done"] = True
        archive_task(client, t)
    data["focuses"] = [f for f in data["focuses"] if f != focus]
    write_task_file(client, data)
    console.print(f"[green]✓ Focus '{focus}' archived ({len(tasks_in_focus)} tasks)[/green]")


# ── LLM inference ─────────────────────────────────────────────────────────────

def is_online() -> bool:
    """Check if we have internet connectivity."""
    try:
        requests.get("https://www.google.com", timeout=3)
        return True
    except Exception:
        return False


def build_inference_prompt(client: str, user_input: str) -> str:
    task_md = client_file(client).read_text() if client_file(client).exists() else "(no tasks yet)"
    data = parse_task_file(client)
    focuses = ", ".join(data["focuses"]) if data["focuses"] else "General"
    descriptions = data.get("descriptions", {})
    focus_context = ""
    if descriptions:
        focus_context = "\nFocus descriptions:\n" + "\n".join(
            f"  - {f}: {d}" for f, d in descriptions.items()
        )
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    next_pri = max((t["priority"] for t in open_tasks), default=0) + 1

    return f"""You are a task management assistant for a consultant.
Current client: {client}
Available focuses: {focuses}{focus_context}
Next available priority number: {next_pri}
Date format: DD.MM.YYYY
Today: {datetime.now().strftime(DATE_FMT)}

CURRENT TASKS:
{task_md}

USER INPUT:
{user_input}

Determine what task operation(s) the user wants. Return a JSON array of operations.
Each operation is an object with an "action" field and relevant parameters.

Available actions:
- add: {{"action": "add", "text": "task description", "focus": "General", "due": "15.05.2026" or null, "recur": "monday" or "weekly" or "monthly" or null, "for": "Sarah" or null, "priority": {next_pri}}}
- edit: {{"action": "edit", "priority": 3, "text": "updated task description"}}
- complete: {{"action": "complete", "priority": 3}}
- delete: {{"action": "delete", "priority": 3}}
- start: {{"action": "start", "priority": 3}}
- block: {{"action": "block", "priority": 3}}
- reset: {{"action": "reset", "priority": 3}}
- reprioritise: {{"action": "reprioritise", "priority": 3, "new_priority": 1}}
- move: {{"action": "move", "priority": 3, "focus": "New Focus"}}
- due: {{"action": "due", "priority": 3, "due": "15.05.2026" or null}}
- recur: {{"action": "recur", "priority": 3, "recur": "monday" or "weekly" or "monthly" or null}}
- for: {{"action": "for", "priority": 3, "for": "Sarah" or null}}
- create_focus: {{"action": "create_focus", "focus": "New Focus Name"}}
- rename_focus: {{"action": "rename_focus", "old": "Old Name", "new": "New Name"}}
- archive_focus: {{"action": "archive_focus", "focus": "Focus Name"}}
- list: {{"action": "list", "focus": null}}
- status: {{"action": "status"}}
- none: {{"action": "none", "response": "conversational reply to user"}}

Supported recurrence values: daily, weekly, fortnightly, monthly, monday, tuesday, wednesday, thursday, friday, saturday, sunday

Use "complete" only when the task was actually done. Use "delete" when the user
wants a task gone without having done it — phrases like "scrap that", "never mind
#3", "forget about the migration task", "remove it", "cancel that one". Both take
it off the open list, but "delete" does not count as finished work.

The "for" field is the person a task was promised to — extract a name from phrases
like "for Sarah", "Sarah asked me to...", "promised John I'd...". Leave it null if
no specific person is mentioned. Use the "for" action to set or clear it on an
existing task (pass "for": null to clear).

For voice dumps or multiple tasks mentioned, return multiple add operations.
If the user is just chatting or asking a question, use "none" with a helpful response.
If unsure about focus, use "General".

Return ONLY a raw JSON array. No explanation."""


def run_inference(client: str, user_input: str) -> list:
    """Call LLM to infer task operations. Uses Anthropic if online, Ollama if not or if force_local."""
    prompt = build_inference_prompt(client, user_input)
    use_cloud = not force_local and bool(ANTHROPIC_API_KEY) and is_online()
    log("info", f"Inference — {'cloud' if use_cloud else 'local'}, input='{user_input[:80]}'")

    try:
        if use_cloud:
            response = requests.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            if not response.ok:
                log("error", f"API error {response.status_code}: {response.text[:500]}")
                console.print(f"[yellow]API error {response.status_code}: {response.text[:300]}[/yellow]")
            response.raise_for_status()
            raw = response.json()["content"][0]["text"].strip()
        else:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
                },
                timeout=120,
            )
            response.raise_for_status()
            raw = response.json()["message"]["content"].strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])

        # Strip thinking tags
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

        # Find the first complete JSON array in the response
        # Use a bracket-counting approach to find the exact end of the array
        start = raw.find("[")
        if start == -1:
            log("warning", f"No JSON array found in response: {raw[:200]}")
            return []

        depth = 0
        end = -1
        for i, ch in enumerate(raw[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end == -1:
            log("warning", f"Unclosed JSON array in response: {raw[:200]}")
            return []

        result = json.loads(raw[start:end])
        log("info", f"Inference result: {result}")
        return result

    except Exception as e:
        log("error", f"Inference failed: {e}")
        console.print(f"[yellow]Inference failed: {e}[/yellow]")
        return []


def execute_operations(client: str, operations: list):
    """Execute a list of inferred task operations."""
    if not operations:
        console.print("[dim]Nothing to do.[/dim]")
        return

    for op in operations:
        action = op.get("action")

        if action == "add":
            add_task(client, op["text"], op.get("focus", "General"), op.get("due"),
                      op.get("recur"), op.get("for"))
        elif action == "for":
            set_for(client, int(op["priority"]), op.get("for"))
        elif action == "complete":
            complete_task(client, int(op["priority"]))
        elif action == "delete":
            delete_task(client, int(op["priority"]))
        elif action == "start":
            set_task_status(client, int(op["priority"]), "in_progress")
        elif action == "block":
            set_task_status(client, int(op["priority"]), "blocked")
        elif action == "reset":
            set_task_status(client, int(op["priority"]), "open")
        elif action == "edit":
            edit_task(client, int(op["priority"]), op["text"])
        elif action == "reprioritise":
            reprioritise_task(client, int(op["priority"]), int(op["new_priority"]))
        elif action == "recur":
            set_recur(client, int(op["priority"]), op.get("recur"))
        elif action == "move":
            move_task_to_focus(client, int(op["priority"]), op["focus"])
        elif action == "due":
            set_due_date(client, int(op["priority"]), op.get("due"))
        elif action == "create_focus":
            create_focus(client, op["focus"])
        elif action == "rename_focus":
            rename_focus(client, op["old"], op["new"])
        elif action == "archive_focus":
            archive_focus(client, op["focus"])
        elif action == "list":
            display_tasks(client, op.get("focus"))
        elif action == "status":
            display_status(client)
        elif action == "none":
            console.print(op.get("response", ""))
        else:
            console.print(f"[yellow]Unknown action: {action}[/yellow]")


def edit_task(client: str, priority: int, new_text: str):
    """Edit the text of an existing task."""
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    match = next((t for t in open_tasks if t["priority"] == priority), None)
    if not match:
        console.print(f"[yellow]No task #{priority}[/yellow]")
        return
    old_text = match["text"]
    match["text"] = new_text.strip()
    write_task_file(client, data)
    log("info", f"Task #{priority} edited: '{old_text}' → '{new_text}'")
    console.print(f"[green]✓ #{priority} updated[/green]")


# ── Reports & review ──────────────────────────────────────────────────────────

STALE_DAYS = 14


def generate_report(client: str, days: int = 7) -> str:
    """Build a plain-text client status update from completed/in-progress/blocked tasks."""
    today = datetime.now()
    cutoff = today - timedelta(days=days)

    archive = core.parse_archive_file(client)
    completed = []
    for entry in archive:
        if entry.get("status") != "completed":
            continue
        try:
            completed_date = datetime.strptime(entry["date"], DATE_FMT)
        except (ValueError, TypeError):
            continue
        if completed_date >= cutoff:
            completed.append(entry)

    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    in_progress = [t for t in open_tasks if t.get("status") == "in_progress"]
    blocked = [t for t in open_tasks if t.get("status") == "blocked"]

    lines = [f"# {client.replace('_', ' ').title()} — Status Update ({today.strftime(DATE_FMT)})", ""]

    lines.append("## Completed this week" if days == 7 else f"## Completed (last {days} days)")
    if completed:
        for entry in completed:
            for_str = f" (for {entry['for']})" if entry.get("for") else ""
            lines.append(f"- {entry['text']}{for_str}")
    else:
        lines.append("- (nothing completed in this period)")
    lines.append("")

    lines.append("## In progress")
    if in_progress:
        for t in in_progress:
            for_str = f" (for {t['for']})" if t.get("for") else ""
            lines.append(f"- {t['text']}{for_str}")
    else:
        lines.append("- (nothing in progress)")
    lines.append("")

    lines.append("## Blocked")
    if blocked:
        for t in blocked:
            for_str = f" (for {t['for']})" if t.get("for") else ""
            lines.append(f"- {t['text']}{for_str}")
    else:
        lines.append("- (nothing blocked)")

    return "\n".join(lines) + "\n"


def save_report(client: str, report: str) -> Path:
    reports_dir = BASE_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{client}_{datetime.now().strftime('%Y%m%d')}.md"
    path.write_text(report)
    return path


def build_review_queue(client: str) -> dict:
    """Categorise open tasks for the weekly review: overdue, stale, blocked, empty focuses."""
    data = parse_task_file(client)
    open_tasks = [t for t in data["tasks"] if not t["done"]]
    today = datetime.now()

    overdue = []
    for t in open_tasks:
        if not t.get("due"):
            continue
        try:
            due_date = datetime.strptime(t["due"], DATE_FMT)
        except ValueError:
            continue
        if due_date < today:
            overdue.append(t)
    overdue.sort(key=lambda t: t["due"])

    stale = []
    for t in open_tasks:
        if t.get("due"):
            continue
        since = t.get("since")
        if not since:
            stale.append(t)  # predates 'since' tracking — age unknown, worth a look
            continue
        try:
            since_date = datetime.strptime(since, DATE_FMT)
        except ValueError:
            stale.append(t)
            continue
        if (today - since_date).days >= STALE_DAYS:
            stale.append(t)
    stale.sort(key=lambda t: t.get("since") or "")

    blocked = [t for t in open_tasks if t.get("status") == "blocked"]

    focuses_with_tasks = {t["focus"] for t in open_tasks}
    empty_focuses = [f for f in data["focuses"] if f not in focuses_with_tasks]

    return {"overdue": overdue, "stale": stale, "blocked": blocked, "empty_focuses": empty_focuses}


def _find_open_task(client: str, focus: str, text: str) -> dict | None:
    """Re-look-up an open task by focus+text — priorities shift on every write_task_file call."""
    data = parse_task_file(client)
    return next((t for t in data["tasks"] if not t["done"] and t["focus"] == focus and t["text"] == text), None)


def review_client(client: str):
    """Interactive walkthrough of overdue, stale, and blocked tasks for one client."""
    queue = build_review_queue(client)

    seen = set()
    reasons = {}
    ordered = []
    for reason, tasks in (("overdue", queue["overdue"]), ("stale", queue["stale"]), ("blocked", queue["blocked"])):
        for t in tasks:
            key = (t["focus"], t["text"])
            if key in seen:
                reasons[key] = reasons[key] + f", {reason}"
                continue
            seen.add(key)
            reasons[key] = reason
            ordered.append(t)

    if not ordered:
        console.print("[green]Nothing overdue, stale, or blocked. Clean sweep.[/green]")
    else:
        console.print(f"[bold]Weekly review — {len(ordered)} task(s) to triage[/bold]\n")
        for t in ordered:
            # Priorities get repacked on every mutation — re-resolve by focus+text
            # rather than trusting the priority captured when the queue was built.
            current = _find_open_task(client, t["focus"], t["text"])
            if not current:
                continue  # already resolved (e.g. completed elsewhere in this loop)

            reason = reasons[(t["focus"], t["text"])]
            due = f" [due {current['due']}]" if current.get("due") else ""
            for_str = f" [for {current['for']}]" if current.get("for") else ""
            console.print(f"[bold]#{current['priority']}[/bold] {current['text']}{due}{for_str}  [dim]({reason})[/dim]")
            choice = Prompt.ask(
                "  [d]one / [x]delete / [s]tart / [b]lock / [r]eset / [u]pdate due / [k]eep / [q]uit",
                default="k"
            ).strip().lower()

            pri = current["priority"]
            if choice == "d":
                complete_task(client, pri)
            elif choice == "x":
                delete_task(client, pri)
            elif choice == "s":
                set_task_status(client, pri, "in_progress")
            elif choice == "b":
                set_task_status(client, pri, "blocked")
            elif choice == "r":
                set_task_status(client, pri, "open")
            elif choice == "u":
                new_due = Prompt.ask("  New due date (DD.MM.YYYY, blank to clear)", default="").strip()
                set_due_date(client, pri, new_due or None)
            elif choice == "q":
                break
            # "k" or anything else — leave as is, move on

    if queue["empty_focuses"]:
        console.print(f"\n[bold]Focuses with no tasks[/bold]\n  " + ", ".join(queue["empty_focuses"]))


# ── Voice ─────────────────────────────────────────────────────────────────────

def voice_start():
    # Remove old files to avoid ffmpeg/recorder complaints
    VOICE_RAW.unlink(missing_ok=True)
    VOICE_16K.unlink(missing_ok=True)
    subprocess.Popen([
        "/data/data/com.termux/files/usr/bin/termux-microphone-record",
        "-f", str(VOICE_RAW),
        "-r", "16000", "-c", "1", "-b", "128", "-l", "0"
    ])
    console.print("[cyan]Recording... /voice off to stop and process.[/cyan]")


def voice_stop() -> str:
    subprocess.run(["/data/data/com.termux/files/usr/bin/termux-microphone-record", "-q"], capture_output=True)
    console.print("[dim]Converting...[/dim]")
    subprocess.run([
        "/data/data/com.termux/files/usr/bin/ffmpeg",
        "-i", str(VOICE_RAW),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(VOICE_16K), "-y"
    ], capture_output=True)
    console.print("[dim]Transcribing...[/dim]")
    result = subprocess.run([
        str(WHISPER_BIN), "-m", str(WHISPER_MODEL),
        "-f", str(VOICE_16K), "--no-timestamps", "-np",
        "--prompt", "consultant, tasks, projects, clients, New Zealand"
    ], capture_output=True, text=True)
    lines = []
    for line in result.stdout.splitlines():
        line = re.sub(r'^\[[\d:.,\s>-]+\]\s*', '', line.strip())
        if line:
            lines.append(line)
    text = " ".join(lines).strip()
    if text:
        console.print(f"[cyan]Voice:[/cyan] {text}")
    return text


# ── Client selection ──────────────────────────────────────────────────────────

def select_client() -> str | None:
    clients = list_clients()
    last = load_last_client()

    console.print("\n[bold]Clients:[/bold]")
    console.print("  [cyan]0[/cyan]  Exit")
    for i, name in enumerate(clients, 1):
        tag = " [dim](last)[/dim]" if name == last else ""
        console.print(f"  [cyan]{i}[/cyan]  {name}{tag}")
    console.print("  [cyan]n[/cyan]  New client")

    if not clients:
        console.print("  [dim](No clients yet — enter 'n' to create one)[/dim]")

    if last and last in clients:
        default = str(clients.index(last) + 1)
    else:
        default = "1" if clients else "n"

    choice = Prompt.ask("\nSelect client", default=default)

    if choice == "0":
        sys.exit(0)
    if choice.lower() == "n":
        name = Prompt.ask("Client name").strip().lower().replace(" ", "_")
        if name:
            client_file(name)  # will be created on first write
            return name
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(clients):
            return clients[idx]
    except ValueError:
        if choice in clients:
            return choice
    return None


# ── Help ──────────────────────────────────────────────────────────────────────

def show_help():
    console.print(Panel(
        "[bold]Just type naturally[/bold] — the assistant will infer what you want.\n\n"
        "[bold]Explicit commands:[/bold]\n"
        "/list [focus]             — show open tasks (shortcut for /task list)\n"
        "/task list [focus]        — show open tasks\n"
        "/task edit <#>            — edit task text\n"
        "/task start <#>           — mark in progress [~]\n"
        "/task block <#>           — mark blocked [!]\n"
        "/task reset <#>           — back to open\n"
        "/task delete <#>          — remove a task without completing it (archived as cancelled)\n"
        "/task pri <#> <new#>      — reprioritise\n"
        "/task move <#> <focus>    — move to focus\n"
        "/task recur <#> <pattern> — set recurrence (daily/weekly/monthly/monday etc)\n"
        "/task recur <#> clear     — remove recurrence\n"
        "/task due <#> clear       — remove due date\n"
        "/task for <#> <name>      — mark who a task was promised to\n"
        "/task for <#> clear       — remove the 'for' tag\n"
        "/focus new <name>         — create focus\n"
        "/focus list               — list focuses\n"
        "/focus rename <old> <new> — rename focus\n"
        "/focus archive <name>     — archive focus\n"
        "/status                   — top priorities, due soon, empty focuses\n"
        "/report [days]            — client status update from completed/in-progress/blocked (default 7 days)\n"
        "/review                   — walk through overdue, stale, and blocked tasks one at a time\n"
        "/switch                   — change client\n"
        "/voice                    — tap to start recording, tap again to stop and process\n"
        "/photo [path]             — extract tasks from latest screenshot (or specified image)\n"
        "/help                     — this message",
        title="Help",
        border_style="blue"
    ))


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    global voice_enabled, force_local
    core.init(BASE_DIR)
    log_file = init_logger()

    console.print(Panel(
        f"[bold]Task Manager[/bold] [dim]v{VERSION}[/dim]\n"
        f"Model: {'Anthropic ' + ANTHROPIC_MODEL if ANTHROPIC_API_KEY else 'Local ' + OLLAMA_MODEL} — use /local to toggle",
        border_style="green"
    ))

    client = select_client()
    if not client:
        sys.exit(0)

    save_last_client(client)
    console.print(f"\n[bold green]Client:[/bold green] {client}")
    display_status(client)

    session = PromptSession(history=FileHistory(str(BASE_DIR / ".task_history")))
    console.print("\n[dim]Type naturally or use /help for commands.[/dim]\n")

    while True:
        try:
            user_input = session.prompt(f"[{client}] > ").strip()
        except (KeyboardInterrupt, EOFError):
            save_last_client(client)
            console.print("\n[bold green]Goodbye.[/bold green]")
            sys.exit(0)

        if not user_input:
            continue

        # ── Explicit commands ──────────────────────────────────────────────────

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=2)
            cmd = parts[0].lower()
            arg1 = parts[1] if len(parts) > 1 else ""
            arg2 = parts[2] if len(parts) > 2 else ""

            if cmd == "/help":
                show_help()

            elif cmd == "/photo":
                screenshots = Path("/storage/emulated/0/DCIM/Screenshots")
                today = datetime.now().strftime("%Y%m%d")
                patterns = [
                    f"Screenshot_{today}_*_OneNote.*",
                    f"Screenshot_{today}_*_Notability.*",
                    f"AISelect_{today}_*_OneNote.*",
                    f"AISelect_{today}_*_Notability.*",
                ]
                images = []
                for pattern in patterns:
                    images += [p for p in screenshots.glob(pattern)
                               if p.suffix.lower() in (".jpg", ".png")]
                images.sort(key=lambda p: p.stat().st_mtime)
                if arg1:
                    images = [Path(arg1)]

                if not images:
                    console.print("[yellow]No OneNote screenshots found for today.[/yellow]")
                else:
                    console.print(f"[dim]Processing {len(images)} screenshot(s)...[/dim]")
                    try:
                        from photo_task import extract_tasks_from_image
                        for img in images:
                            summary = extract_tasks_from_image(client, img)
                            console.print(f"[green]✓ {summary}[/green]")
                            img.unlink()
                            console.print(f"[dim]Deleted {img.name}[/dim]")
                        display_tasks(client)
                    except Exception as e:
                        console.print(f"[yellow]Photo processing failed: {e}[/yellow]")

            elif cmd == "/report":
                try:
                    days = int(arg1) if arg1 else 7
                except ValueError:
                    days = 7
                report = generate_report(client, days)
                print(report)
                path = save_report(client, report)
                console.print(f"[dim]Saved to {path}[/dim]")

            elif cmd == "/review":
                review_client(client)

            elif cmd == "/local":
                force_local = not force_local
                state = "on — using local Ollama" if force_local else "off — using Anthropic"
                console.print(f"[cyan]Local mode {state}.[/cyan]")

            elif cmd == "/exit":
                save_last_client(client)
                console.print("[bold green]Goodbye.[/bold green]")
                sys.exit(0)

            elif cmd == "/switch":
                client = select_client() or client
                save_last_client(client)
                console.print(f"[bold green]Client:[/bold green] {client}")
                display_status(client)

            elif cmd == "/status":
                display_status(client)

            elif cmd == "/voice":
                voice_enabled = not voice_enabled
                if voice_enabled:
                    voice_start()
                else:
                    text = voice_stop()
                    if text:
                        ops = run_inference(client, text)
                        execute_operations(client, ops)
                    else:
                        console.print("[dim]Voice off — no audio captured.[/dim]")

            elif cmd == "/list":
                display_tasks(client, arg1 or None)

            elif cmd == "/task":
                subcmd = arg1.lower()
                if subcmd == "add":
                    # Parse: /task add <text> [focus:<name>] [due:DD.MM.YYYY] [for:<name>]
                    text = arg2
                    focus = "General"
                    due = None
                    for_person = None
                    focus_match = re.search(r'focus:(\S+)', text)
                    due_match = re.search(r'due:([\d.]+)', text)
                    for_match = re.search(r'for:(\S+)', text)
                    if focus_match:
                        focus = focus_match.group(1)
                        text = text.replace(focus_match.group(0), "").strip()
                    if due_match:
                        due = due_match.group(1)
                        text = text.replace(due_match.group(0), "").strip()
                    if for_match:
                        for_person = for_match.group(1)
                        text = text.replace(for_match.group(0), "").strip()
                    if text:
                        add_task(client, text, focus, due, for_person=for_person)
                    else:
                        console.print("[yellow]Usage: /task add <description> [focus:<name>] [due:DD.MM.YYYY] [for:<name>][/yellow]")
                elif subcmd == "list":
                    display_tasks(client, arg2 or None)
                elif subcmd == "start":
                    try:
                        set_task_status(client, int(arg2), "in_progress")
                    except ValueError:
                        console.print("[yellow]Usage: /task start <#>[/yellow]")
                elif subcmd == "block":
                    try:
                        set_task_status(client, int(arg2), "blocked")
                    except ValueError:
                        console.print("[yellow]Usage: /task block <#>[/yellow]")
                elif subcmd in ("reset",):
                    try:
                        set_task_status(client, int(arg2), "open")
                    except ValueError:
                        console.print("[yellow]Usage: /task reset <#>[/yellow]")
                elif subcmd == "edit":
                    try:
                        pri = int(arg2)
                        data = parse_task_file(client)
                        open_tasks = [t for t in data["tasks"] if not t["done"]]
                        match = next((t for t in open_tasks if t["priority"] == pri), None)
                        if match:
                            console.print(f"[dim]Current: {match['text']}[/dim]")
                            new_text = Prompt.ask("New text").strip()
                            if new_text:
                                edit_task(client, pri, new_text)
                        else:
                            console.print(f"[yellow]No task #{pri}[/yellow]")
                    except ValueError:
                        console.print("[yellow]Usage: /task edit <#>[/yellow]")
                elif subcmd == "recur":
                    nums = arg2.split(maxsplit=1)
                    if len(nums) == 2:
                        try:
                            pri = int(nums[0])
                            recur_arg = nums[1].strip().lower()
                            recur = None if recur_arg == "clear" else recur_arg
                            set_recur(client, pri, recur)
                        except ValueError:
                            console.print("[yellow]Usage: /task recur <#> <daily|weekly|monthly|monday...> or clear[/yellow]")
                    else:
                        console.print("[yellow]Usage: /task recur <#> <pattern> or clear[/yellow]")
                elif subcmd == "done":
                    try:
                        complete_task(client, int(arg2))
                    except ValueError:
                        console.print("[yellow]Usage: /task done <number>[/yellow]")
                elif subcmd in ("delete", "cancel"):
                    try:
                        delete_task(client, int(arg2))
                    except ValueError:
                        console.print("[yellow]Usage: /task delete <number>[/yellow]")
                elif subcmd == "pri":
                    nums = arg2.split()
                    if len(nums) == 2:
                        try:
                            reprioritise_task(client, int(nums[0]), int(nums[1]))
                        except ValueError:
                            console.print("[yellow]Usage: /task pri <#> <new#>[/yellow]")
                    else:
                        console.print("[yellow]Usage: /task pri <#> <new#>[/yellow]")
                elif subcmd == "move":
                    nums = arg2.split(maxsplit=1)
                    if len(nums) == 2:
                        try:
                            move_task_to_focus(client, int(nums[0]), nums[1])
                        except ValueError:
                            console.print("[yellow]Usage: /task move <#> <focus>[/yellow]")
                    else:
                        console.print("[yellow]Usage: /task move <#> <focus>[/yellow]")
                elif subcmd == "due":
                    nums = arg2.split(maxsplit=1)
                    if len(nums) == 2:
                        try:
                            pri = int(nums[0])
                            date_arg = nums[1].strip().lower()
                            due = None if date_arg == "clear" else date_arg
                            set_due_date(client, pri, due)
                        except ValueError:
                            console.print("[yellow]Usage: /task due <#> <DD.MM.YYYY> or /task due <#> clear[/yellow]")
                    else:
                        console.print("[yellow]Usage: /task due <#> <DD.MM.YYYY> or /task due <#> clear[/yellow]")
                elif subcmd == "for":
                    nums = arg2.split(maxsplit=1)
                    if len(nums) == 2:
                        try:
                            pri = int(nums[0])
                            name_arg = nums[1].strip()
                            for_person = None if name_arg.lower() == "clear" else name_arg
                            set_for(client, pri, for_person)
                        except ValueError:
                            console.print("[yellow]Usage: /task for <#> <name> or /task for <#> clear[/yellow]")
                    else:
                        console.print("[yellow]Usage: /task for <#> <name> or /task for <#> clear[/yellow]")
                else:
                    console.print("[yellow]Usage: /task list|done|delete|pri|move|due|for[/yellow]")

            elif cmd == "/focus":
                subcmd = arg1.lower()
                if subcmd == "new":
                    if arg2:
                        desc = Prompt.ask(f"Description for '{arg2}' (optional)", default="")
                        create_focus(client, arg2, desc)
                    else:
                        console.print("[yellow]Usage: /focus new <name>[/yellow]")
                elif subcmd == "list":
                    data = parse_task_file(client)
                    open_tasks = [t for t in data["tasks"] if not t["done"]]
                    descriptions = data.get("descriptions", {})
                    for f in data["focuses"]:
                        count = sum(1 for t in open_tasks if t["focus"] == f)
                        desc = f" [dim]— {descriptions[f]}[/dim]" if f in descriptions else ""
                        console.print(f"  [cyan]{f}[/cyan] ({count} tasks){desc}")
                elif subcmd == "rename":
                    names = arg2.split(maxsplit=1)
                    if len(names) == 2:
                        rename_focus(client, names[0], names[1])
                    else:
                        console.print("[yellow]Usage: /focus rename <old> <new>[/yellow]")
                elif subcmd == "archive":
                    archive_focus(client, arg2)
                else:
                    console.print("[yellow]Usage: /focus new|list|rename|archive[/yellow]")

            else:
                console.print(f"[yellow]Unknown command. Type /help.[/yellow]")

            continue

        # ── Natural language inference ─────────────────────────────────────────

        ops = run_inference(client, user_input)
        execute_operations(client, ops)


if __name__ == "__main__":
    main()
