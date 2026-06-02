#!/usr/bin/env bash
set -euo pipefail

HEXFEED_DIR="${HEXFEED_DIR:-$HOME/.local/share/hexfeed}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

PYTHON="${PYTHON:-python3}"

echo "  ⬡ Installing hexfeed..."

# Check Python version
PY_VER=$($PYTHON --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
REQUIRED="3.11"
if [ "$(echo -e "$PYTHON_VER\n$REQUIRED" | sort -V | head -1)" = "$PYTHON_VER" ] && [ "$PYTHON_VER" != "$REQUIRED" ]; then
    echo "  ✗ Python >= 3.11 required (found $PYTHON_VER)"
    exit 1
fi

# Check for tkinter / textual deps (ubuntu/debian)
if command -v apt-get &>/dev/null; then
    PKGS="python3 python3-pip python3-venv build-essential tor"
    echo "  Detected apt-based system. Installing system dependencies..."
    sudo apt-get update -qq && sudo apt-get install -y -qq $PKGS
fi

# Check for tor on arch
if command -v pacman &>/dev/null; then
    if ! command -v tor &>/dev/null; then
        echo "  hexfeed recommends tor for optional .onion support."
        echo "  Install it: sudo pacman -S tor"
    fi
fi

mkdir -p "$HEXFEED_DIR" "$BIN_DIR"

if [ ! -d "$HEXFEED_DIR/.venv" ]; then
    echo "  Creating virtual environment..."
    $PYTHON -m venv "$HEXFEED_DIR/.venv"
fi

echo "  Installing Python dependencies..."
"$HEXFEED_DIR/.venv/bin/pip" install --quiet --upgrade pip setuptools wheel

# Install in editable mode from the script's location
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
"$HEXFEED_DIR/.venv/bin/pip" install --quiet -e "$PROJECT_DIR"

# Symlink binaries
ln -sf "$HEXFEED_DIR/.venv/bin/hexfeed" "$BIN_DIR/hexfeed"
ln -sf "$HEXFEED_DIR/.venv/bin/hexfeed-server" "$BIN_DIR/hexfeed-server"

# Create .admin_password placeholder
if [ ! -f "$HEXFEED_DIR/.admin_password" ]; then
    touch "$HEXFEED_DIR/.admin_password"
fi

echo "  ✓ hexfeed installed!"
echo
echo "  Make sure $BIN_DIR is in your PATH:"
echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
echo
echo "  To start the server:"
echo "    hexfeed-server"
echo
echo "  To start the client:"
echo "    hexfeed"
echo
