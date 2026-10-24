#!/bin/bash
# Double-click to start PhotoStory on a Mac. The first run sets things up (a minute or two).
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv || { echo "Python 3 is needed: install it from https://www.python.org/downloads/"; read; exit 1; }
  .venv/bin/pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.txt || { echo "Setup failed, see above."; read; exit 1; }
fi
.venv/bin/python -m photostory
