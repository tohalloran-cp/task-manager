#!/data/data/com.termux/files/usr/bin/bash
# deploy.sh — copies task-related files from this repo checkout to their
# runtime locations, then runs the test suites.
#
# Usage (after "git pull" inside the repo):
#   bash code/deploy.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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
        cp "$src" "$dst"
        chmod "$perms" "$dst"
        echo "  ✓ $(basename "$src")"
    fi
}

# Python scripts → Documents
deploy "$SCRIPT_DIR/tasks_core.py"      "$DOCS/tasks_core.py"
deploy "$SCRIPT_DIR/tasks.py"           "$DOCS/tasks.py"
deploy "$SCRIPT_DIR/test_tasks_core.py" "$DOCS/test_tasks_core.py"
deploy "$SCRIPT_DIR/test_tasks.py"      "$DOCS/test_tasks.py"
deploy "$SCRIPT_DIR/voice_dump.py"      "$DOCS/voice_dump.py"
deploy "$SCRIPT_DIR/photo_task.py"      "$DOCS/photo_task.py"
deploy "$SCRIPT_DIR/morning_brief.py"   "$DOCS/morning_brief.py"

# Shell scripts → Documents
deploy "$SCRIPT_DIR/tasks_run.sh"  "$DOCS/tasks_run.sh"  755

# Widget scripts → ~/.shortcuts/tasks (background, no terminal)
deploy "$SCRIPT_DIR/voice_start.sh" "$SHORTCUTS/voice_start.sh" 755
deploy "$SCRIPT_DIR/voice_dump.sh"  "$SHORTCUTS/voice_dump.sh"  755
deploy "$SCRIPT_DIR/photo_task.sh"  "$SHORTCUTS/photo_task.sh"  755

# Widget scripts → ~/.shortcuts (foreground, opens terminal)
deploy "$SCRIPT_DIR/tasks_widget.sh"        ~/.shortcuts/tasks_widget.sh        755
deploy "$SCRIPT_DIR/morning_brief_show.sh"  ~/.shortcuts/morning_brief_show.sh  755

# Remove retired files from previous deploys, if present
for stale in "$DOCS/chat.py" "$DOCS/run.sh"; do
    if [ -f "$stale" ]; then
        rm -f "$stale"
        echo "  ✗ removed retired file: $(basename "$stale")"
    fi
done

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
