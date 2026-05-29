#!/usr/bin/env bash
set -euo pipefail

HEXFEED_DIR="${HEXFEED_DIR:-$HOME/.local/share/hexfeed}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

echo "  ⬡ Installing hexfeed on macOS..."

# Install Homebrew if not present
if ! command -v brew &>/dev/null; then
    echo "  Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

echo "  Installing Python 3.11+..."
brew install python@3.12 tor 2>/dev/null || true

mkdir -p "$HEXFEED_DIR" "$BIN_DIR"

if [ ! -d "$HEXFEED_DIR/.venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$HEXFEED_DIR/.venv"
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
echo "  Add to your ~/.zshrc:"
echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
echo
