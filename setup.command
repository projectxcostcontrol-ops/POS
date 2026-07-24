#!/bin/bash
# Double-click this file in Finder to set everything up for the first time.
# (If macOS blocks it: right-click -> Open, then confirm once.)

cd "$(dirname "$0")"
ROOT="$(pwd)"

echo "== Setting up backend =="
cd "$ROOT/backend"
if [ ! -d venv ]; then
  python3.12 -m venv venv || python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "!! Created backend/.env - open it and add your LOYVERSE_ACCESS_TOKEN before running start.command !!"
  echo ""
fi
deactivate

echo "== Setting up frontend =="
cd "$ROOT/frontend"
npm install

echo ""
echo "Setup complete. Next:"
echo "1. Edit backend/.env and add your Loyverse token (if you haven't already)"
echo "2. Double-click start.command to launch everything"
echo ""
read -p "Press Enter to close this window..."
