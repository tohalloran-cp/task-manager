#!/data/data/com.termux/files/usr/bin/bash
# voice_start.sh — called by Termux:Widget to start recording
# Place this file in ~/.shortcuts/tasks/ for silent background execution

source ~/.envvars

LOG="/storage/emulated/0/Documents/ai-assistant/logs/voice_start.log"
mkdir -p "$(dirname $LOG)"
echo "--- $(date) ---" >> "$LOG"

VOICE_RAW="/data/data/com.termux/files/usr/tmp/voice_raw.wav"

# Delete old files — force fresh recording every time
echo "Removing old files..." >> "$LOG"
rm -f "$VOICE_RAW"
rm -f "/data/data/com.termux/files/usr/tmp/voice_16k.wav"

# Start recording — unlimited duration
echo "Starting recording to $VOICE_RAW" >> "$LOG"
termux-microphone-record -f "$VOICE_RAW" -r 16000 -c 1 -b 128 -l 0
echo "Recording started" >> "$LOG"
