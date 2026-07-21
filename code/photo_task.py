#!/usr/bin/env python3
"""
photo_task.py — Extract tasks from an image using Claude vision.
Takes a screenshot of handwritten notes and adds tasks to the active client.

Version history:
    1.0  Initial release — Claude vision extracts tasks from images
         Supports handwritten notes, whiteboards, printed text
         Files to last active client, outputs summary for notification
    1.5  Prompt accepts any red marking around text — circles, boxes, wiggly lines, incomplete outlines

Usage:
    python photo_task.py <image_path> [client]
    python photo_task.py /storage/emulated/0/DCIM/Screenshots/screenshot.jpg
"""

VERSION = "1.5"

import os
import re
import sys
import json
import base64
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


# ── Vision processing ─────────────────────────────────────────────────────────

def image_to_base64(image_path: Path) -> tuple[str, str]:
    """Convert image to base64. Returns (base64_data, media_type)."""
    suffix = image_path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_types.get(suffix, "image/jpeg")
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


def extract_tasks_from_image(client: str, image_path: Path) -> str:
    """
    Send image to Claude vision, extract tasks and add them to the client file.
    Returns a summary string for notification.
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

    prompt = f"""You are extracting tasks from a photo of handwritten notes.
Client: {client}
Available focuses: {focuses}{focus_context}
Next available priority number: {next_pri}
Date format: DD.MM.YYYY
Today: {datetime.now().strftime(DATE_FMT)}

CURRENT TASKS:
{task_md}

Look carefully at the handwritten notes in the image.
Extract text that has red drawn around it or near it — this includes circles,
squares, wiggly lines, incomplete outlines, or any red pen marking that
appears to surround or frame a piece of text, even loosely.
Ignore text with no red marking anywhere near it.
If nothing has red around it, return an empty task list.

For each marked item, treat it as a task or action item.
If a due date appears near the marked text, include it.

Return a JSON object with two keys:
"tasks": array of task operations to add
"summary": short plain text summary for a notification, include client name and specific tasks

Each task:
{{"action": "add", "text": "task description", "focus": "General", "due": null, "recur": null, "priority": {next_pri}}}

Use the most appropriate focus based on the content and focus descriptions.
If a due date is visible near the marked text, include it in DD.MM.YYYY format.

Example response:
{{"tasks": [{{"action": "add", "text": "Talk to James about the roadmap", "focus": "General", "due": null, "recur": null, "priority": {next_pri}}}], "summary": "{client}: Added 1 task — Talk to James about the roadmap"}}

If nothing has red around it, return:
{{"tasks": [], "summary": "{client}: No red-marked tasks found"}}

Respond with raw JSON only."""

    img_data, media_type = image_to_base64(image_path)

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
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_data,
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }],
        },
        timeout=60,
    )
    response.raise_for_status()
    raw = response.json()["content"][0]["text"].strip()

    # Extract JSON
    start = raw.find("{")
    if start == -1:
        return f"{client}: Could not parse response"

    depth = 0
    end = -1
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        return f"{client}: Malformed response"

    result = json.loads(raw[start:end])
    tasks = result.get("tasks", [])
    summary = result.get("summary", f"{client}: Processed image")

    # Apply tasks
    for op in tasks:
        if op.get("action") == "add":
            data = parse_task_file(client)
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
                "recur": op.get("recur"),
                "done": False,
                "status": "open",
            })
            write_task_file(client, data)

    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python photo_task.py <image_path> [client]")
        sys.exit(1)

    image_path = Path(sys.argv[1])
    client = sys.argv[2].strip() if len(sys.argv) > 2 else None

    core.init(BASE_DIR)

    if not client:
        client = core.load_last_client()

    if not client:
        print("Error: no client specified and no last client found.")
        sys.exit(1)

    if not image_path.exists():
        print(f"Error: image not found: {image_path}")
        sys.exit(1)

    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    try:
        summary = extract_tasks_from_image(client, image_path)
        print(summary)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
