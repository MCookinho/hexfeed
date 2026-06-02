#!/usr/bin/env bash
# hexfeed — Oracle Cloud auto-setup
# Usage: curl -fsSL https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/setup-oracle.sh | bash
set -euo pipefail

GREEN='\033[92m'
CYAN='\033[96m'
DIM='\033[90m'
RESET='\033[0m'
BOLD='\033[1m'

echo "  ═══ hexfeed — Oracle Cloud auto-setup ═══"
echo

# ── Root check ──
if [ "$EUID" -eq 0 ]; then
  echo "  ✗ Don't run as root. Run as ubuntu user."
  exit 1
fi

# ── 1. System dependencies ──
echo "  → Installing Tor + Python..."
sudo apt update -qq
sudo apt install -y -qq tor python3 python3-pip python3-venv git

# ── 2. Create hexfeed user ──
echo "  → Creating hexfeed user..."
sudo useradd -r -s /bin/false hexfeed 2>/dev/null || true
sudo mkdir -p /opt/hexfeed
sudo chown hexfeed:hexfeed /opt/hexfeed

# ── 3. Install hexfeed ──
echo "  → Installing hexfeed..."
sudo -u hexfeed python3 -m venv /opt/hexfeed/venv
sudo -u hexfeed /opt/hexfeed/venv/bin/pip install -q setuptools wheel
sudo -u hexfeed git clone --depth 1 https://github.com/MCookinho/hexfeed.git /opt/hexfeed/src
sudo -u hexfeed /opt/hexfeed/venv/bin/pip install -q -e /opt/hexfeed/src

sudo ln -sf /opt/hexfeed/venv/bin/hexfeed /usr/local/bin/hexfeed
sudo ln -sf /opt/hexfeed/venv/bin/hexfeed-server /usr/local/bin/hexfeed-server

# ── 3b. Create writable directories ──
echo "  → Setting up data directories..."
sudo mkdir -p /opt/hexfeed/src/data /opt/hexfeed/src/uploads/avatars
sudo mkdir -p /opt/hexfeed/data /opt/hexfeed/uploads
sudo chown -R hexfeed:hexfeed /opt/hexfeed
sudo usermod -a -G hexfeed ubuntu
sudo chmod g+rwxs /opt/hexfeed/src/data /opt/hexfeed/src/uploads /opt/hexfeed/src/uploads/avatars
sudo chmod g+rwxs /opt/hexfeed/data /opt/hexfeed/uploads

# Restart hexfeed to pick up directory changes
sudo systemctl restart hexfeed 2>/dev/null || true

# ── 4. Systemd service ──
echo "  → Creating systemd service..."
sudo tee /etc/systemd/system/hexfeed.service > /dev/null << 'SVC'
[Unit]
Description=hexfeed RSS Reader
After=network.target tor.service
Wants=tor.service

[Service]
Type=simple
User=hexfeed
Group=hexfeed
ExecStart=/usr/local/bin/hexfeed-server --port 8080
WorkingDirectory=/opt/hexfeed/src
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
# Hexfeed precisa criar data/ e uploads/ em tempo de execução
ProtectSystem=strict
ReadWritePaths=/opt/hexfeed /home/hexfeed/.config/hexfeed
ProtectHome=read-only
CapabilityBoundingSet=
SystemCallFilter=@system-service

[Install]
WantedBy=multi-user.target
SVC

sudo systemctl daemon-reload
sudo systemctl enable hexfeed
sudo systemctl start hexfeed

# ── 5. Tor hidden service ──
echo "  → Configuring Tor .onion..."
OS=$(grep -oP '^ID=\K.*' /etc/os-release 2>/dev/null || echo "ubuntu")

sudo mkdir -p /var/lib/tor/hexfeed
sudo chown -R debian-tor:debian-tor /var/lib/tor/hexfeed
sudo chmod 700 /var/lib/tor/hexfeed

# Remove old config if exists, add fresh
sudo sed -i '/# hexfeed hidden service/,/^$/d' /etc/tor/torrc 2>/dev/null || true
echo "" | sudo tee -a /etc/tor/torrc > /dev/null
echo "# hexfeed hidden service" | sudo tee -a /etc/tor/torrc > /dev/null
echo "HiddenServiceDir /var/lib/tor/hexfeed/" | sudo tee -a /etc/tor/torrc > /dev/null
echo "HiddenServicePort 80 127.0.0.1:8080" | sudo tee -a /etc/tor/torrc > /dev/null

sudo systemctl restart tor

# ── 6. Wait for .onion ──
echo "  → Waiting for .onion address..."
for i in $(seq 1 30); do
  if [ -f /var/lib/tor/hexfeed/hostname ]; then
    ONION=$(sudo cat /var/lib/tor/hexfeed/hostname)
    break
  fi
  sleep 2
done

echo ""
echo -e "  ═══════════════════════════════════════"
echo -e "   ${GREEN}✓ hexfeed is RUNNING${RESET}"
echo -e "   ${GREEN}✓ Tor .onion is ACTIVE${RESET}"
echo ""
echo -e "   🧅 ${CYAN}http://$ONION${RESET}"
echo ""
echo -e "   ${DIM}Commands:${RESET}"
echo -e "   ${DIM}  logs:    journalctl -u hexfeed -f${RESET}"
echo -e "   ${DIM}  restart: systemctl restart hexfeed${RESET}"
echo -e "   ${DIM}  update:  cd /opt/hexfeed/src && git pull && systemctl restart hexfeed${RESET}"
echo -e "   ${DIM}  test:    hexfeed-server  (logout/login first for group permissions)${RESET}"
echo -e "  ═══════════════════════════════════════"
echo
