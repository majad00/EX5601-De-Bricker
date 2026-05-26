#!/bin/sh

PORT="${1:-/dev/ttyUSB0}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
python3 "$SCRIPT_DIR/debricker_menu.py" "$PORT"
