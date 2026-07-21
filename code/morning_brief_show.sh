#!/data/data/com.termux/files/usr/bin/bash
# morning_brief_show.sh — shows last morning brief in terminal
# Place in ~/.shortcuts/ (opens terminal)

source ~/.envvars

BRIEF="/storage/emulated/0/Documents/ai-assistant/logs/morning_brief.txt"

if [ -f "$BRIEF" ]; then
    cat "$BRIEF"
    echo ""
    echo "Press any key to exit..."
    read -n 1
else
    echo "No morning brief yet."
    echo "Run morning_brief.py to generate one."
    read -n 1
fi
