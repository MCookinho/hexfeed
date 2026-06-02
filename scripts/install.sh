#!/usr/bin/env bash
# hexfeed bootstrap installer
# Works even without Python installed.
# Usage: curl -fsSL https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/install.sh | bash
set -euo pipefail

HEXFEED_URL="https://github.com/MCookinho/hexfeed"
CLONE_DIR="/tmp/hexfeed-install-$$"

cleanup() { rm -rf "$CLONE_DIR"; }
trap cleanup EXIT

echo "  ⬡ hexfeed — Bootstrap installer"
echo

# ── 1. Check / install Python ──
install_python() {
    if command -v python3 &>/dev/null; then
        PY_VER=$(python3 --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        REQ="3.11"
        if [ "$(echo -e "$PY_VER\n$REQ" | sort -V | head -1)" = "$REQ" ] || [ "$PY_VER" = "$REQ" ]; then
            echo "  ✓ Python $PY_VER found"
            return 0
        fi
        echo "  ⚠ Python $PY_VER too old (need 3.11+)"
    fi

    echo "  Installing Python 3.11+..."

    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3 python3-pip python3-venv
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm --needed python python-pip
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3 python3-pip python3-venv
    elif command -v zypper &>/dev/null; then
        sudo zypper install -y python3 python3-pip python3-venv
    elif command -v brew &>/dev/null; then
        brew install python@3.12
    else
        echo "  ✗ Could not install Python automatically."
        echo "  Install Python 3.11+ from https://python.org and re-run."
        exit 1
    fi
}

install_python

# ── 2. Clone repo ──
echo "  Cloning hexfeed..."
if ! git clone --depth 1 "$HEXFEED_URL.git" "$CLONE_DIR" 2>/dev/null; then
    if command -v curl &>/dev/null; then
        echo "  git not found, downloading tarball..."
        curl -fsSL "$HEXFEED_URL/archive/refs/heads/main.tar.gz" | tar -xz -C /tmp
        CLONE_DIR="/tmp/hexfeed-main"
    else
        echo "  ✗ Need git or curl. Install one of them and re-run."
        exit 1
    fi
fi

# ── 3. Run the Python installer ──
echo "  Running hexfeed installer..."
python3 "$CLONE_DIR/scripts/install.py" "$@"
