#!/bin/bash
set -e

cd "$(dirname "$0")"   # always run from ~/track

# Kill any old instances
pkill -f "webhook_catcher.py" 2>/dev/null || true
pkill -f "http.server 8000" 2>/dev/null || true

echo "Starting webhook catcher..."
python3 webhook_catcher.py > catcher.log 2>&1 &
CATCHER_PID=$!

echo "Starting web server on http://localhost:8000 ..."
python3 -m http.server 8000 > server.log 2>&1 &
SERVER_PID=$!

echo ""
echo "Running:"
echo "  Catcher PID: $CATCHER_PID  (logs → catcher.log)"
echo "  Server  PID: $SERVER_PID   (logs → server.log)"
echo ""
echo "Open: http://localhost:8000/alerts.html"
echo "Password: BustedBetch77"
echo ""
echo "To stop everything:"
echo "  kill $CATCHER_PID $SERVER_PID"
echo ""
echo "Press Ctrl-C to exit this script (processes keep running in background)"

# Keep the script alive so Ctrl-C can be used to stop both
trap "kill $CATCHER_PID $SERVER_PID 2>/dev/null; exit" INT
wait
