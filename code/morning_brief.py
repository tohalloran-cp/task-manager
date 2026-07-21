#!/usr/bin/env python3
"""
morning_brief.py — Daily morning brief across all clients.
Fires a termux-notification with top priorities, overdue, and due today.
Designed to be run via cron.

Version history:
    1.0  Initial release — scans all clients, fires notification
         Top 5 priorities across all clients
         Overdue and due-today highlighted
         Focuses with no tasks flagged
         No API call — pure markdown parsing, works offline
    1.1  Annotate overdue/due-today/top5/due-soon lines with "(for Name)"
         when a task's [for ...] tag is set
"""

VERSION = "1.1"

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/storage/emulated/0/Documents/ai-assistant")

# Source envvars for API key etc (not needed here but good practice)
import tasks_core as core
core.init(BASE_DIR)

DATE_FMT = core.DATE_FMT


def run_brief():
    today = datetime.now()
    clients = core.list_clients()

    if not clients:
        notify("Morning Brief", "No clients found.")
        return

    overdue      = []
    due_today    = []
    due_soon     = []
    top_tasks    = []
    empty_focuses = []

    for client in clients:
        data = core.parse_task_file(client)
        open_tasks = [t for t in data["tasks"] if not t["done"]]

        for t in open_tasks:
            t["_client"] = client
            top_tasks.append(t)

            if t.get("due"):
                try:
                    due_date = datetime.strptime(t["due"], DATE_FMT)
                    days = (due_date - today).days
                    if days < 0:
                        overdue.append((abs(days), t))
                    elif days == 0:
                        due_today.append(t)
                    elif days <= 7:
                        due_soon.append((days, t))
                except ValueError:
                    pass

        # Focuses with no tasks
        focuses_with_tasks = {t["focus"] for t in open_tasks}
        for focus in data["focuses"]:
            if focus not in focuses_with_tasks:
                empty_focuses.append(f"{client}/{focus}")

    # Sort all open tasks by priority across clients
    top_tasks.sort(key=lambda t: t["priority"])
    top5 = top_tasks[:5]

    # Build notification content
    lines = []

    if overdue:
        overdue.sort(key=lambda x: x[0], reverse=True)
        lines.append(f"⚠️ OVERDUE ({len(overdue)})")
        for days, t in overdue[:3]:
            for_str = f" (for {t['for']})" if t.get("for") else ""
            lines.append(f"  #{t['priority']} [{t['_client']}] {t['text']} ({days}d){for_str}")

    if due_today:
        lines.append(f"📅 DUE TODAY ({len(due_today)})")
        for t in due_today[:3]:
            for_str = f" (for {t['for']})" if t.get("for") else ""
            lines.append(f"  #{t['priority']} [{t['_client']}] {t['text']}{for_str}")

    lines.append(f"🎯 TOP 5 PRIORITIES")
    for t in top5:
        due_str = f" [{t['due']}]" if t.get("due") else ""
        for_str = f" (for {t['for']})" if t.get("for") else ""
        lines.append(f"  #{t['priority']} [{t['_client']}] {t['text']}{due_str}{for_str}")

    if due_soon:
        due_soon.sort(key=lambda x: x[0])
        lines.append(f"📆 DUE THIS WEEK ({len(due_soon)})")
        for days, t in due_soon[:3]:
            for_str = f" (for {t['for']})" if t.get("for") else ""
            lines.append(f"  #{t['priority']} [{t['_client']}] {t['text']} (in {days}d){for_str}")

    if empty_focuses:
        lines.append(f"💤 EMPTY FOCUSES: {', '.join(empty_focuses[:3])}")

    title = f"Morning Brief — {today.strftime('%a %d %b')}"
    content = "\n".join(lines)

    # Fire notification
    notify(title, content)

    # Also write to a file for the terminal widget to display
    brief_file = BASE_DIR / "logs" / "morning_brief.txt"
    brief_file.parent.mkdir(parents=True, exist_ok=True)
    brief_file.write_text(f"{title}\n{'─' * 40}\n{content}\n")


def notify(title: str, content: str):
    subprocess.run([
        "termux-notification",
        "--title", title,
        "--content", content,
        "--ongoing",
        "--id", "morning-brief",
    ], capture_output=True)


if __name__ == "__main__":
    run_brief()
