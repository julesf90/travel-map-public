#!/bin/bash
# Double-click to start the travel map and open it in your browser.
cd "$(dirname "$0")" || exit 1
URL="http://localhost:8765"

if lsof -ti tcp:8765 >/dev/null 2>&1; then
  echo "The travel map is already running. Opening $URL"
  open "$URL"
  exit 0
fi

( sleep 1; open "$URL" ) &
echo "Leave this window open while you use the map. Close it (or press Ctrl+C) to stop."
exec python3 server.py
