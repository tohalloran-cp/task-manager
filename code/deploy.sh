#!/data/data/com.termux/files/usr/bin/bash
# deploy.sh — moves task-related files from Downloads to correct locations
# Run after downloading updated files from Claude

DOWNLOADS=~/storage/downloads
DOCS=/storage/emulated/0/Documents
SHORTCUTS=~/.shortcuts/tasks

echo "Deploying files..."

# Ensure directories exist
mkdir -p "$SHORTCUTS"
mkdir -p ~/.shortcuts

deploy() {
    local src="$1"
    local dst="$2"
    local perms="${3:-644}"
    if [ -f "$src" ]; then
        mv "$src" "$dst"
        chmod "$perms" "$dst"
        echo "  ✓ $(basename $src)"
    fi
}

# Python scripts → Documents
deploy "$DOWNLOADS/tasks_core.py"      "$DOCS/tasks_core.py"
deploy "$DOWNLOADS/tasks.py"           "$DOCS/tasks.py"
deploy "$DOWNLOADS/test_tasks_core.py" "$DOCS/test_tasks_core.py"
deploy "$DOWNLOADS/test_tasks.py"      "$DOCS/test_tasks.py"
deploy "$DOWNLOADS/voice_dump.py"      "$DOCS/voice_dump.py"
deploy "$DOWNLOADS/photo_task.py"      "$DOCS/photo_task.py"
deploy "$DOWNLOADS/morning_brief.py"   "$DOCS/morning_brief.py"

# Shell scripts → Documents
deploy "$DOWNLOADS/tasks_run.sh"  "$DOCS/tasks_run.sh"  755

# Widget scripts → ~/.shortcuts/tasks (background, no terminal)
deploy "$DOWNLOADS/voice_start.sh" "$SHORTCUTS/voice_start.sh" 755
deploy "$DOWNLOADS/voice_dump.sh"  "$SHORTCUTS/voice_dump.sh"  755
deploy "$DOWNLOADS/photo_task.sh"  "$SHORTCUTS/photo_task.sh"  755

# Widget scripts → ~/.shortcuts (foreground, opens terminal)
deploy "$DOWNLOADS/tasks_widget.sh"        ~/.shortcuts/tasks_widget.sh        755
deploy "$DOWNLOADS/morning_brief_show.sh"  ~/.shortcuts/morning_brief_show.sh  755

# Run tests
run_tests() {
    local file="$1"
    local name="$2"
    if [ -f "$file" ]; then
        echo ""
        echo "Running $name..."
        echo "─────────────────────────────────────"
        python "$file" -v
        return $?
    fi
    return 0
}

PASS=0
run_tests "$DOCS/test_tasks_core.py" "core tests" || PASS=1
run_tests "$DOCS/test_tasks.py"      "tasks tests" || PASS=1

echo "─────────────────────────────────────"
if [ $PASS -eq 0 ]; then
    echo "✓ All tests passed."
else
    echo "✗ Tests failed — review output above before using."
fi

echo ""
echo "Done."
