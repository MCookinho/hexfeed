#!/usr/bin/env bash
set -euo pipefail

echo "  ⬡ hexfeed - Arch Linux manual installer"
echo

# You can also install from AUR: yay -S hexfeed
echo "  This script installs directly from source."
echo "  For AUR: yay -S hexfeed"
echo

HEXFEED_DIR="${HEXFEED_DIR:-$HOME/.local/share/hexfeed}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

echo "  Installing system dependencies..."
sudo pacman -S --noconfirm --needed python python-pip tor

mkdir -p "$HEXFEED_DIR" "$BIN_DIR"

if [ ! -d "$HEXFEED_DIR/.venv" ]; then
    echo "  Creating virtual environment..."
    python -m venv "$HEXFEED_DIR/.venv"
fi

echo "  Installing Python dependencies..."
"$HEXFEED_DIR/.venv/bin/pip" install --quiet --upgrade pip setuptools wheel

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
"$HEXFEED_DIR/.venv/bin/pip" install --quiet -e "$PROJECT_DIR"

ln -sf "$HEXFEED_DIR/.venv/bin/hexfeed" "$BIN_DIR/hexfeed"
ln -sf "$HEXFEED_DIR/.venv/bin/hexfeed-server" "$BIN_DIR/hexfeed-server"

echo "  ✓ hexfeed installed!"
echo
echo "  Make sure $BIN_DIR is in your PATH:"
echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
echo
