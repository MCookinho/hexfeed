#!/usr/bin/env bash
# hexfeed — Local machine auto-setup
# One-command: curl -fsSL https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/setup-local.sh | bash
set -euo pipefail

GREEN='\033[92m'
CYAN='\033[96m'
DIM='\033[90m'
RESET='\033[0m'
BOLD='\033[1m'

echo "  ═══ hexfeed — Local auto-setup ═══"
echo

# ── Distro detection ──
install_packages() {
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip python3-venv git tor
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm --needed python python-pip git tor
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y python3 python3-pip python3-virtualenv git tor
  else
    echo "  ⚠️  Unsupported package manager. Install python3, pip, git, tor manually."
    exit 1
  fi
}

# ── 1. Install deps ──
echo "  → Installing system dependencies (Tor, Python, Git)..."
install_packages

# ── 2. Clone hexfeed ──
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/hexfeed}"
mkdir -p "$INSTALL_DIR"
echo "  → Cloning hexfeed to $INSTALL_DIR..."
if [ -d "$INSTALL_DIR/src" ]; then
  echo "  → Updating existing clone..."
  cd "$INSTALL_DIR/src" && git pull --ff-only
else
  git clone --depth 1 https://github.com/MCookinho/hexfeed.git "$INSTALL_DIR/src"
fi

# ── 3. Create venv + install ──
echo "  → Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip setuptools wheel
"$INSTALL_DIR/venv/bin/pip" install -q -e "$INSTALL_DIR/src"

# ── 4. Symlink binaries ──
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/venv/bin/hexfeed" "$BIN_DIR/hexfeed"
ln -sf "$INSTALL_DIR/venv/bin/hexfeed-server" "$BIN_DIR/hexfeed-server"

# ── 5. Create data dirs ──
mkdir -p "$INSTALL_DIR/src/data" "$INSTALL_DIR/src/uploads" "$INSTALL_DIR/src/uploads/avatars"

# ── 6. Systemd service (optional) ──
SERVICE=""
if command -v systemctl &>/dev/null; then
  echo
  echo -n "  → Create systemd service for auto-start? [Y/n] "
  read -r REPLY
  if [[ ! "$REPLY" =~ ^[Nn] ]]; then
    SERVICE="hexfeed"
    tee /tmp/hexfeed.service > /dev/null << 'SVC'
[Unit]
Description=hexfeed RSS Reader
After=network.target tor.service
Wants=tor.service

[Service]
Type=simple
User=PLACEHOLDER_USER
ExecStart=PLACEHOLDER_BIN/hexfeed-server --port 8080
WorkingDirectory=PLACEHOLDER_DIR/src
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=PLACEHOLDER_DIR
ProtectHome=read-only
CapabilityBoundingSet=
SystemCallFilter=@system-service

[Install]
WantedBy=multi-user.target
SVC
    sed -i "s|PLACEHOLDER_USER|$USER|" /tmp/hexfeed.service
    sed -i "s|PLACEHOLDER_BIN|$INSTALL_DIR/venv/bin|" /tmp/hexfeed.service
    sed -i "s|PLACEHOLDER_DIR|$INSTALL_DIR|" /tmp/hexfeed.service
    sudo mv /tmp/hexfeed.service /etc/systemd/system/hexfeed.service
    sudo systemctl daemon-reload
    sudo systemctl enable hexfeed
    sudo systemctl restart hexfeed
    echo "  ✓ hexfeed.service created and started"
  fi
fi

# ── 7. Wait for Tor onion ──
ONION=""
if [ -n "$SERVICE" ]; then
  echo "  → Waiting for Tor onion service (may take 2 min on first run)..."
  for i in $(seq 1 60); do
    ONION=$(curl -s http://127.0.0.1:8080/api/onion 2>/dev/null | grep -oP '"onion_address":\s*"\K[^"]+' || true)
    if [ -n "$ONION" ]; then
      break
    fi
    sleep 3
  done
fi

# ── Output ──
echo
echo -e "  ═══════════════════════════════════════════"
echo -e "   ${GREEN}✓ hexfeed installed${RESET}"

if [ -n "$SERVICE" ]; then
  echo -e "   ${GREEN}✓ systemd service active (port 8080)${RESET}"
fi
if [ -n "$ONION" ]; then
  echo -e "   ${GREEN}✓ Tor onion service active${RESET}"
  echo
  echo -e "   🧅 ${CYAN}http://$ONION${RESET}"
else
  echo
  echo -e "   ${DIM}Tor auto-starts when you run:${RESET}"
  echo -e "   ${DIM}  hexfeed-server${RESET}"
  echo -e "   ${DIM}  (tor binary is installed — onion auto-detected)${RESET}"
fi

echo
echo -e "   ${DIM}Commands:${RESET}"
if [ -n "$SERVICE" ]; then
  echo -e "   ${DIM}  status:  systemctl status hexfeed${RESET}"
  echo -e "   ${DIM}  logs:    journalctl -u hexfeed -f${RESET}"
  echo -e "   ${DIM}  stop:    systemctl stop hexfeed${RESET}"
fi
echo -e "   ${DIM}  run:     hexfeed-server${RESET}"
echo -e "   ${DIM}  client:  hexfeed${RESET}"
echo -e "   ${DIM}  onion:   curl http://127.0.0.1:8080/api/onion${RESET}"
echo -e "  ═══════════════════════════════════════════"
echo
echo -e "  ${DIM}Add to PATH (if needed):${RESET}"
echo -e "  ${DIM}  export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
echo
