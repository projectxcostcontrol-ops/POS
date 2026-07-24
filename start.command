#!/bin/bash
# Double-click this file in Finder to launch everything.
# (If macOS blocks it: right-click -> Open, then confirm once.)
# Opens 3 Terminal windows: Firebase emulator, backend, frontend.

cd "$(dirname "$0")"
ROOT="$(pwd)"

osascript <<EOF
tell application "Terminal"
  activate
  do script "cd \"$ROOT\" && firebase emulators:start"
  do script "cd \"$ROOT/backend\" && source venv/bin/activate && uvicorn api.main:app --reload"
  do script "cd \"$ROOT/frontend\" && npm run dev"
end tell
EOF
