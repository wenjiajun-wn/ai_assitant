#!/bin/bash
cd "$(dirname "$0")"
echo "AI TODO Assistant - Starting..."
echo ""
echo "Starting screenshot watcher..."
echo "Cmd+Shift+4 to capture - AI will auto-analyze"
echo "Close this window to stop"
echo ""
python3 hotkey_screenshot.py
