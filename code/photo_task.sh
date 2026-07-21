#!/data/data/com.termux/files/usr/bin/bash
# photo_task.sh — processes all today's OneNote screenshots as tasks, then deletes them
# Place this file in ~/.shortcuts/tasks/ for silent background execution

source ~/.envvars

LOG="/storage/emulated/0/Documents/ai-assistant/logs/photo_task.log"
mkdir -p "$(dirname $LOG)"
echo "--- $(date) ---" >> "$LOG"

SCREENSHOTS="/storage/emulated/0/DCIM/Screenshots"
PHOTO_TASK="/storage/emulated/0/Documents/photo_task.py"
TODAY=$(date +%Y%m%d)

# Find all today's screenshots from supported note apps — both naming patterns
IMAGES=$(ls "$SCREENSHOTS"/Screenshot_${TODAY}_*_OneNote.jpg \
            "$SCREENSHOTS"/Screenshot_${TODAY}_*_OneNote.png \
            "$SCREENSHOTS"/Screenshot_${TODAY}_*_Notability.jpg \
            "$SCREENSHOTS"/Screenshot_${TODAY}_*_Notability.png \
            "$SCREENSHOTS"/AISelect_${TODAY}_*_OneNote.jpg \
            "$SCREENSHOTS"/AISelect_${TODAY}_*_OneNote.png \
            "$SCREENSHOTS"/AISelect_${TODAY}_*_Notability.jpg \
            "$SCREENSHOTS"/AISelect_${TODAY}_*_Notability.png 2>/dev/null)

if [ -z "$IMAGES" ]; then
    echo "No OneNote screenshots found for today ($TODAY)" >> "$LOG"
    termux-notification --title "Photo Task" --content "No OneNote screenshots found for today"
    exit 0
fi

COUNT=$(echo "$IMAGES" | wc -l)
echo "Found $COUNT OneNote screenshot(s) for today" >> "$LOG"
termux-notification --title "Photo Task" --content "Processing $COUNT screenshot(s)..."

# Process each screenshot and collect summaries
ALL_SUMMARIES=""
PROCESSED=0
FAILED=0

for IMG in $IMAGES; do
    echo "Processing: $IMG" >> "$LOG"
    SUMMARY=$(python "$PHOTO_TASK" "$IMG" 2>> "$LOG")
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "✓ $SUMMARY" >> "$LOG"
        ALL_SUMMARIES="$ALL_SUMMARIES$SUMMARY\n"
        rm -f "$IMG"
        echo "Deleted: $IMG" >> "$LOG"
        PROCESSED=$((PROCESSED + 1))
    else
        echo "✗ Failed: $IMG — $SUMMARY" >> "$LOG"
        FAILED=$((FAILED + 1))
    fi
done

# Build final notification
if [ $FAILED -eq 0 ]; then
    NOTIFICATION=$(echo -e "$ALL_SUMMARIES" | grep -v "^$" | tr '\n' ' ')
    termux-notification --title "Photo Task ✓ ($PROCESSED processed)" --content "$NOTIFICATION"
else
    NOTIFICATION=$(echo -e "$ALL_SUMMARIES" | grep -v "^$" | tr '\n' ' ')
    termux-notification --title "Photo Task ($PROCESSED ok, $FAILED failed)" --content "$NOTIFICATION"
fi

echo "Done. Processed: $PROCESSED, Failed: $FAILED" >> "$LOG"
