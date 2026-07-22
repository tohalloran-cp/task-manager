# Task Manager — Claude Code Context

## What this is

A purpose-built task management system for a consultant working across multiple clients.
Runs on an Android phone (Samsung S24 Ultra) via Termux. The primary interface is a
terminal app, with home screen widgets for voice capture and photo processing.

The owner is an agile coach and independent consultant.
He works across multiple clients and focuses per client, and wants to be the person who
always does what he says he'll do.

---

## File map

```
tasks_core.py          Shared core — all file I/O, parsing, writing, recurrence
tasks.py               Main interactive CLI — commands, inference, voice, photo
voice_dump.py          Non-interactive voice transcript processor (called by widget)
photo_task.py          Vision-based task extractor from screenshots (called by widget)
morning_brief.py       Daily notification — top priorities, overdue, due today
deploy.sh              Copies files from git checkout to correct locations, runs tests
tasks_run.sh           Launches tasks.py (sources envvars, starts Ollama as fallback)
tasks_widget.sh        Home screen widget → opens terminal with tasks.py
voice_start.sh         Home screen widget → starts microphone recording (background)
voice_dump.sh          Home screen widget → stops recording, transcribes, processes
photo_task.sh          Home screen widget → processes today's Notability screenshots
morning_brief_show.sh  Home screen widget → shows last morning brief in terminal
test_tasks_core.py     Tests for tasks_core.py
test_tasks.py          Tests for tasks.py, voice_dump.py, photo_task.py
```

---

## Architecture

### tasks_core.py — the foundation

All file operations live here. Every other Python script imports this module.

```python
import tasks_core as core
core.init(base_dir)   # must be called before anything else
```

`init(base_dir)` sets `BASE_DIR` and `TASKS_DIR` at runtime. This means:
- Production code passes the real path
- Tests pass a temp directory — full isolation, never touches production data

Key functions:
- `parse_task_file(client)` → dict with focuses, descriptions, tasks
- `write_task_file(client, data)` — repacks priorities, writes markdown
- `archive_task(client, task, status="completed")` — appends to `client_archive.md`; status is `"completed"` or `"cancelled"`
- `parse_archive_file(client)` → list of archived tasks (text, due, for, status, date)
- `next_recur_date(recur, from_date, due_date)` — calculates next occurrence
- `list_clients()`, `client_file()`, `archive_file()`
- `load_last_client()`, `save_last_client()`

### Data format

One markdown file per client in `TASKS_DIR`:

```markdown
# Client Name

## General
- [ ] #1 Task description [due 15.05.2026] [for Sarah] [since 01.05.2026]
- [~] #2 In progress task
- [!] #3 Blocked task

## Focus Name
<!-- Focus description used for inference context -->
- [ ] #4 Task in focus [due 20.05.2026] [every monday]
```

Statuses: `[ ]` open, `[~]` in progress, `[!]` blocked, `[x]` done (archived)
Priority: global across client, auto-repacked on every write (consecutive, no gaps)
Due dates: DD.MM.YYYY format
Recurrence: `[every monday]`, `[every weekly]`, `[every 3 months]` etc
`for`: optional — who the task was promised to (commitment tracking)
`since`: creation date, stamped automatically on every new task (used for staleness in `/review`)

Archive file is a flat list. `[x]` = actually completed, `[-]` = deleted/cancelled
without being done — kept distinct so `/report` never counts a scrapped task as
finished work:
```markdown
# Client Name — Archive
- [x] Task text [due 15.05.2026] [for Sarah] [completed 10.05.2026]
- [-] Abandoned task [cancelled 11.05.2026]
```

### Inference

`tasks.py` uses the Anthropic API (claude-sonnet-4-6) for natural language task operations.
Falls back to local Ollama (qwen3:1.7b) when offline or `/local` is toggled.

The model returns a JSON array of operations:
```json
[{"action": "add", "text": "...", "focus": "General", "due": null, "recur": null}]
[{"action": "complete", "priority": 3}]
[{"action": "reprioritise", "priority": 3, "new_priority": 1}]
```

Supported actions: add, edit, complete, delete, start, block, reset, reprioritise,
move, due, recur, for, create_focus, rename_focus, archive_focus, list, status, none

`delete` (aka `/task delete <#>` / `/task cancel <#>`) removes a task without marking
it done — for when it's just not going to happen. Distinct from `complete`.

`/report [days]` and `/review` are explicit-only commands (not inference actions) —
a client status update and an interactive overdue/stale/blocked triage walkthrough,
scoped to the current client.

---

## Android/Termux constraints

- **Python packages**: no Rust-compiled packages (no `anthropic` SDK — use raw `requests`)
- **API calls**: always use `requests.post` directly to `https://api.anthropic.com/v1/messages`
- **File paths**: production data at `/storage/emulated/0/Documents/ai-assistant/`
- **Temp files**: `/data/data/com.termux/files/usr/tmp/`
- **Binaries**: full paths required — `/data/data/com.termux/files/usr/bin/ffmpeg` etc
- **Environment**: API key in `~/.envvars`, sourced by all scripts
- **Widget scripts**: background (no terminal) go in `~/.shortcuts/tasks/`, foreground in `~/.shortcuts/`
- **Termux:Widget**: Termux:Tasker plugin doesn't work on Android 14 without root

## Screenshot processing

Photos are taken from Notability (previously OneNote). Naming convention:
```
Screenshot_YYYYMMDD_HHMMSS_Notability.jpg
AISelect_YYYYMMDD_HHMMSS_Notability.jpg
```

`photo_task.sh` finds all today's matching screenshots, processes each via Claude vision,
then deletes them. The vision prompt looks for text with red markings around it (circles,
boxes, squiggly lines — anything red near the text counts).

## Voice pipeline

1. `voice_start.sh` — `termux-microphone-record` starts recording to `/tmp/voice_raw.wav`
2. `voice_dump.sh` — stops recording, converts with ffmpeg, transcribes with whisper.cpp
3. `voice_dump.py` — sends transcript to Anthropic API with client context, extracts tasks

Whisper model: `ggml-medium.en.bin` at `~/whisper.cpp/models/`
Whisper binary: `~/whisper.cpp/build/bin/whisper-cli`

---

## Coding conventions

### Version history
Every Python file must have a version history in the docstring:
```python
Version history:
    1.0  Initial release
    1.1  Added X
    1.2  Fixed Y bug — brief description of what broke and why
```
Bump version and add an entry for every change, no matter how small.

### No API keys in code
API key comes from `os.environ.get("ANTHROPIC_API_KEY", "")`. Never hardcode it.

### Test isolation
Every test method uses a unique client name:
```python
self.client = f"test_{self._testMethodName}"
```
This prevents state bleeding between tests. Tests call `core.init(tmp)` with a temp
directory — never the production path.

### Function merging bug
A recurring issue: `str_replace` edits have accidentally merged separate functions
into one. Always verify function boundaries after edits. The test suite catches this
because merged functions cause NameErrors or AttributeErrors.

### Deploy pattern
`deploy.sh` **copies** files from the git checkout (`code/`) to their correct
runtime locations (`Documents/`, `~/.shortcuts/`). The repo checkout is the
source of truth — `git pull` updates it, `deploy.sh` never modifies it.

---

## Test suites

Two test suites, both run automatically by `deploy.sh`:

**test_tasks_core.py** — tests for tasks_core.py:
- TestInit, TestListClients, TestLastClient
- TestParseTaskFile, TestWriteTaskFile, TestArchiveTask, TestParseArchiveFile
- TestNextRecurDate (all patterns + edge cases)

**test_tasks.py** — tests for tasks.py:
- TestParseRoundTrip, TestPriorityRepack, TestAddTask
- TestCompleteTask (including recurrence), TestDeleteTask
- TestTaskMutations, TestReprioritise, TestFocusOperations
- TestListClients, TestMockedInference, TestPhotoTask
- TestReport, TestReview

Run locally:
```bash
python test_tasks_core.py -v
python test_tasks.py -v
```

**Rule**: every new feature needs tests. Every bug fix needs a regression test.
Both test files must be updated in the same PR/commit as the feature.

---

## Deploy workflow

Repo: https://github.com/tohalloran-cp/task-manager

1. Edit in Claude Code on laptop, commit, `git push`
2. On phone (Termux), run the `deploy` alias → `git pull` in the repo checkout,
   then `bash code/deploy.sh` to copy files to their runtime locations and run
   both test suites
3. Repo checkout lives at `~/task-manager`; `deploy.sh` copies out from
   `~/task-manager/code/` — the checkout itself is never the runtime location

---

## Morning brief

`morning_brief.py` runs via cron at 7:30am Mon–Thu.
Scans all client task files, fires a `termux-notification` with:
- Overdue tasks
- Due today
- Top 5 priorities across all clients
- Due this week
- Focuses with no tasks

No API call — pure markdown parsing, works offline.
Output also written to `logs/morning_brief.txt` for the terminal widget.

---

## What's been deliberately avoided

- **Anthropic Python SDK** — can't install on Termux (Rust dependency). Use raw requests.
- **SQLite** — markdown files are simpler, Obsidian-compatible, human-readable
- **Tasker deep integration** — Android 14 blocks plugin permissions without root
- **Large local models** — 1.7b models are unreliable for structured JSON output
- **Conversation memory in tasks.py** — stateless by design, each request is self-contained
