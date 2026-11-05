#!/bin/bash
# Double-click this in Finder to start the Subpart Cutter.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "First run: setting up, this takes a minute or two."
    python3 -m venv .venv || exit 1
fi
if ! .venv/bin/python -c "import PySide6" 2>/dev/null; then
    .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
exec .venv/bin/python -m shipyard.cutter
