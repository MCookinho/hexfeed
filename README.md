# Hexfeed

> **Anonymous** private social network in your terminal. Tor-native, zero IP leaks, zero trackers.

![](https://img.shields.io/badge/python-%3E%3D3.11-blue)
![](https://img.shields.io/badge/license-MIT-green)
![](https://img.shields.io/badge/status-beta-yellow)
[![](https://img.shields.io/badge/aur-hexfeed-blue)](https://aur.archlinux.org/packages/hexfeed)

```
🧅 Tor onion — no real IP ever exposed
🔒 No IPs stored — login attempts, logs, rate limiters all IP-free
📁 No filenames — uploads stored as UUIDs, original names never leak
🕵️ No Server header — no version fingerprinting
📧 No email — no PII collected or exposed
```

---

## Client

### How it works

hexfeed is a **client-server** app. The server runs on your machine (or a remote VM), and you connect to it with the **terminal UI client**. All communication goes through **Tor** — your IP is never exposed.

### Download

Choose your platform and run the command. Installs everything (Tor, Python, hexfeed):

| Platform | Command |
|----------|---------|
| **Linux** | `curl -fsSL https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/install.sh \| bash` |
| **macOS** | `curl -fsSL https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/install-macos.sh \| bash` |
| **Windows** | `powershell -c "iwr https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/install.ps1 -OutFile install.ps1; .\install.ps1"` |
| **Arch** | `yay -S hexfeed` |

### Run

```bash
hexfeed
```

On first launch, it asks for the server address. If you're running locally, press Enter (defaults to `127.0.0.1:8000`). If connecting to a remote `.onion`, paste the address.

Register an account from the TUI and you're in.

---

## Server

Run your own hexfeed server so you and your friends have your own private social network.

### Quick start (Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/setup-local.sh | bash
```

This installs Tor, Python, hexfeed, and creates a systemd service (optional). At the end it prints your 🧅 **.onion address**.

### Oracle Cloud Free Tier

```bash
curl -fsSL https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/setup-oracle.sh | bash
```

Same thing, deployed on Oracle's always-free Ampere A1 (4 CPU, 24GB RAM).

### Manual install

```bash
git clone https://github.com/MCookinho/hexfeed.git
cd hexfeed
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Start

```bash
hexfeed-server
```

Output:
```
  ⬡ hexfeed:         http://127.0.0.1:8080
  🧅 Starting Tor...
  🧅 Onion service active: http://your-random-address.onion
  🧅 Share this address for external access.
```

### First access

1. Connect with the client: `hexfeed`
2. Register an account from the TUI
3. Open the admin panel at **http://127.0.0.1:8001/admin**
4. Set your admin password on first access

Share your `.onion` address with friends:

```bash
curl http://127.0.0.1:8080/api/onion
```

---

## Anonymity Model

| Attack Vector | Protection |
|---|---|
| **Server IP discovery** | Server binds to `127.0.0.1`. External access **only** via `.onion`. No `--host 0.0.0.0`. |
| **IP logging** | `login_attempts` and `security_events` tables have **no IP column**. Zero user IPs persisted. |
| **Access logs** | uvicorn `access_log=False`, `log_level=warning`. No request logging with IPs. |
| **Server fingerprinting** | `Server` header stripped from all HTTP responses. |
| **Rate limiter IP tracking** | All IPs hashed with per-boot random salt before storage in memory. |
| **Email / PII** | No email field. Not collected at registration. |
| **File metadata** | Original filenames never stored. UUIDs only. |
| **Filesystem paths** | Avatar paths stored as relative. |
| **Traffic analysis** | Full Tor onion service with ephemeral key persistence. |

## Features

- **100% Anonymous** — No IPs, no logs, no Server header, no PII
- **Tor-native** — Auto-start `.onion` hidden service when `tor` is installed
- **Terminal UI** — Fast, keyboard-driven client built with [Textual](https://textual.textualize.io)
- **Self-hosted** — Your data, your server. No cloud, no surveillance
- **Encrypted DMs** — End-to-end encrypted direct messages using PGP
- **Admin panel** — Web-based admin at `:8001`
- **Proof-of-Work** — Hashcash-style PoW + math challenge to prevent bot registration
- **i18n** — Multi-language interface (Portuguese, English)
- **Minimalist** — No JavaScript frontend. Just a Python server + TUI client

## Architecture

```
Internet ──> Tor Network ──> .onion ──> 127.0.0.1:8080 ──> FastAPI ──> SQLite
Local    ──> 127.0.0.1:8080
LAN      ──> ❌ Blocked by design
```

```
┌──────────────┐     HTTP/JSON      ┌──────────────┐
│  TUI Client   │ ────────────────>  │  FastAPI      │
│  (Textual)    │ <────────────────  │  Server       │
└──────────────┘                    └───────┬──────┘
                                            │
                                    ┌───────┴───────┐
                                    │  SQLite3       │
                                    │  (no IP store) │
                                    └───────────────┘
```

## License

MIT License. See [LICENSE](LICENSE).

---

> Built with Python, [Textual](https://textual.textualize.io), and [FastAPI](https://fastapi.tiangolo.com).
> **Anonymity is not a feature. It's a requirement.**
