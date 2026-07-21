#!/usr/bin/env python3
"""
voice_dump.py — Process a voice transcript and update task files.
Called by Tasker after whisper transcription. Non-interactive.

Usage:
    python voice_dump.py "<transcript>" [client_name]

    If client_name is omitted, uses the last active client from tasks.py.

Version history:
    1.0  Initial release — Tasker-callable voice dump processor
         Reads transcript from argument, updates task markdown via Anthropic API
         Outputs summary line for Tasker notification
    1.1  Summary now includes client name and specific task names
    1.3  Replaced real client name in prompt example with a placeholder
"""

VERSION = "1.3"

import os
import re
import sys
import json
import requests
from datetime import datetime
from pathlib import Path
import tasks_core as core

# ── Configuration ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = "claude-sonnet-4-6"
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"

BASE_DIR          = Path("/storage/emulated/0/Documents/ai-assistant")
DATE_FMT          = core.DATE_FMT

# Convenience aliases
def client_file(client):    return core.client_file(client)
def archive_file(client):   return core.archive_file(client)
def load_last_client():     return core.load_last_client()
def parse_task_file(c):     return core.parse_task_file(c)
def write_task_file(c, d):  return core.write_task_file(c, d)
def archive_task(c, t):     return core.archive_task(c, t)


# ── Anthropic call ────────────────────────────────────────────────────────────

def process_transcript(client: str, transcript: str) -> str:
    """
    Send transcript to Anthropic, extract task operations, apply them.
    Returns a summary string for Tasker to show as a notification.
    """
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
    task_md = client_file(client).read_text() if client_file(client).exists() else "(no tasks yet)"

    prompt = f"""You are processing a voice note from a consultant to update their task list.
Client: {client}
Available focuses: {focuses}{focus_context}
Next available priority number: {next_pri}
Date format: DD.MM.YYYY
Today: {datetime.now().strftime(DATE_FMT)}

CURRENT TASKS:
{task_md}

VOICE NOTE TRANSCRIPT:
{transcript}

Extract all task operations from this voice note. The consultant may mention:
- New tasks to add
- Tasks they've completed
- Tasks that are blocked
- Tasks they've started working on
- Priority changes

Return a JSON object with two keys:
"operations": array of operations (same format as before)
"summary": a plain text summary for a phone notification. Include the client name ({client}), and list each specific task added, completed, or updated by name. Keep it concise but specific. Example: "acme_corp: Added 'Chase James on architecture sign-off' to Platform Migration. Marked #3 complete."
- add: {{"action": "add", "text": "task description", "focus": "General", "due": null, "priority": {next_pri}}}
- complete: {{"action": "complete", "priority": 3}}
- start: {{"action": "start", "priority": 3}}
- block: {{"action": "block", "priority": 3}}
- reset: {{"action": "reset", "priority": 3}}
- reprioritise: {{"action": "reprioritise", "priority": 3, "new_priority": 1}}

Example response:
{{"operations": [{{"action": "add", "text": "Chase James on architecture sign-off", "focus": "Platform Migration", "due": null, "priority": {next_pri}}}], "summary": "Added 1 task: Chase James on architecture sign-off."}}

Respond with raw JSON only."""

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
    response.raise_for_status()
    raw = response.json()["content"][0]["text"].strip()

    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])

    result = json.loads(raw.strip())
    operations = result.get("operations", [])
    summary = result.get("summary", "Voice note processed.")

    # Apply operations
    added = 0
    completed = 0
    for op in operations:
        action = op.get("action")
        data = parse_task_file(client)  # reload each time for freshness

        if action == "add":
            open_t = [t for t in data["tasks"] if not t["done"]]
            next_p = max((t["priority"] for t in open_t), default=0) + 1
            focus = op.get("focus", "General")
            if focus not in data["focuses"]:
                data["focuses"].append(focus)
            data["tasks"].append({
                "priority": next_p,
                "focus": focus,
                "text": op["text"],
                "due": op.get("due"),
                "done": False,
                "status": "open",
            })
            write_task_file(client, data)
            added += 1

        elif action == "complete":
            open_t = [t for t in data["tasks"] if not t["done"]]
            match = next((t for t in open_t if t["priority"] == int(op["priority"])), None)
            if match:
                match["done"] = True
                archive_task(client, match)
                write_task_file(client, data)
                completed += 1

        elif action in ("start", "block", "reset"):
            status_map = {"start": "in_progress", "block": "blocked", "reset": "open"}
            open_t = [t for t in data["tasks"] if not t["done"]]
            match = next((t for t in open_t if t["priority"] == int(op["priority"])), None)
            if match:
                match["status"] = status_map[action]
                write_task_file(client, data)

        elif action == "reprioritise":
            open_t = [t for t in data["tasks"] if not t["done"]]
            open_t.sort(key=lambda t: t["priority"])
            match = next((t for t in open_t if t["priority"] == int(op["priority"])), None)
            if match:
                new_p = max(1, min(int(op["new_priority"]), len(open_t)))
                open_t.remove(match)
                open_t.insert(new_p - 1, match)
                for i, t in enumerate(open_t, 1):
                    t["priority"] = i
                write_task_file(client, data)

    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python voice_dump.py \"<transcript>\" [client]")
        sys.exit(1)

    transcript = sys.argv[1].strip()
    client = sys.argv[2].strip() if len(sys.argv) > 2 else None

    core.init(BASE_DIR)

    if not client:
        client = core.load_last_client()

    if not client:
        print("Error: no client specified and no last client found.")
        sys.exit(1)

    if not transcript:
        print("Error: empty transcript.")
        sys.exit(1)

    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    try:
        summary = process_transcript(client, transcript)
        # Print summary — Tasker reads stdout for the notification
        print(summary)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
