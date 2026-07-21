#!/data/data/com.termux/files/usr/bin/bash

# Load environment variables including ANTHROPIC_API_KEY
source ~/.envvars
export OLLAMA_KEEP_ALIVE=-1

# ── Start Ollama (offline fallback) ───────────────────────────────────────────
echo "Starting Ollama..."
ollama serve &
OLLAMA_PID=$!
sleep 3

# ── Start task manager ────────────────────────────────────────────────────────
echo "Starting task manager..."
python /storage/emulated/0/Documents/tasks.py

# ── Cleanup ───────────────────────────────────────────────────────────────────
echo "Shutting down Ollama..."
kill $OLLAMA_PID 2>/dev/null
