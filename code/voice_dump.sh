#!/data/data/com.termux/files/usr/bin/bash
# voice_dump.sh — called by Termux:Widget to stop recording, transcribe and process
# Place this file in ~/.shortcuts/tasks/ for silent background execution

source ~/.envvars

LOG="/storage/emulated/0/Documents/ai-assistant/logs/voice_dump.log"
mkdir -p "$(dirname $LOG)"
echo "--- $(date) ---" >> "$LOG"

FFMPEG="/data/data/com.termux/files/usr/bin/ffmpeg"
WHISPER="/data/data/com.termux/files/home/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL="/data/data/com.termux/files/home/whisper.cpp/models/ggml-medium.en.bin"
VOICE_RAW="/data/data/com.termux/files/usr/tmp/voice_raw.wav"
VOICE_16K="/data/data/com.termux/files/usr/tmp/voice_16k.wav"
VOICE_DUMP="/storage/emulated/0/Documents/voice_dump.py"

# Step 1 — Stop recording
echo "Stopping recording..." >> "$LOG"
termux-microphone-record -q
sleep 2
echo "Recording stopped" >> "$LOG"

# Step 2 — Convert audio
echo "Converting audio..." >> "$LOG"
$FFMPEG -i "$VOICE_RAW" -ar 16000 -ac 1 -c:a pcm_s16le "$VOICE_16K" -y >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "ERROR: ffmpeg conversion failed" >> "$LOG"
    termux-notification --title "Voice Dump Failed" --content "Audio conversion failed — check logs"
    exit 1
fi
echo "Conversion done" >> "$LOG"

# Step 3 — Transcribe
echo "Transcribing..." >> "$LOG"
TRANSCRIPT=$($WHISPER -m "$WHISPER_MODEL" -f "$VOICE_16K" --no-timestamps -np --prompt "consultant, tasks, projects, clients, New Zealand" 2>> "$LOG")
echo "Transcript: $TRANSCRIPT" >> "$LOG"

if [ -z "$TRANSCRIPT" ]; then
    echo "ERROR: empty transcript" >> "$LOG"
    termux-notification --title "Voice Dump Failed" --content "Transcription returned nothing — check logs"
    exit 1
fi

# Step 4 — Process transcript and update tasks
echo "Processing with API..." >> "$LOG"
SUMMARY=$(python "$VOICE_DUMP" "$TRANSCRIPT" 2>> "$LOG")
EXIT_CODE=$?
echo "API exit code: $EXIT_CODE" >> "$LOG"
echo "Summary: $SUMMARY" >> "$LOG"

if [ $EXIT_CODE -ne 0 ]; then
    termux-notification --title "Voice Dump Failed" --content "$SUMMARY"
    exit 1
fi

# Step 5 — Show notification
termux-notification --title "Voice Dump" --content "$SUMMARY"
echo "Done" >> "$LOG"
