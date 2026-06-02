# Hexfeed

> **Anonymous** private social network in your terminal. Tor-native, zero IP leaks, zero trackers.

hexfeed is a **self-hosted**, **anonymous-by-design** social networking platform with a **terminal UI** client. No JavaScript, no tracking, no centralized servers — just you, your friends, and your terminal.

```
🧅 Tor onion service — no real IP ever exposed
🔒 No IPs stored — login attempts, logs, rate limiters all IP-free
📁 No filenames — uploads stored as UUIDs, original names never leak
🕵️ No Server header — no version fingerprinting
📧 No email — no PII collected or exposed
```

![](https://img.shields.io/badge/python-%3E%3D3.11-blue)
![](https://img.shields.io/badge/license-MIT-green)
![](https://img.shields.io/badge/status-beta-yellow)
[![](https://img.shields.io/badge/aur-hexfeed-blue)](https://aur.archlinux.org/packages/hexfeed)

---

## Anonymity Model

| Attack Vector | Protection |
|---|---|
| **Server IP discovery** | Server binds exclusively to `127.0.0.1`. External access **only** via `.onion`. No `--host 0.0.0.0` possible. |
| **IP logging** | `login_attempts` and `security_events` tables have **no IP column**. Zero user IPs persisted. |
| **Access logs** | uvicorn `access_log=False`, `log_level=warning`. No request logging with IPs. |
| **Server fingerprinting** | `Server` header stripped from all HTTP responses. No version leaks. |
| **Rate limiter IP tracking** | All IPs hashed with per-boot random salt before storage in memory. |
| **Email / PII** | No email field in API responses. Not collected at registration. |
| **File metadata** | Original filenames never stored or returned. UUIDs only. |
| **Filesystem paths** | Avatar paths stored as relative. No absolute path leaks. |
| **Admin panel** | In-memory admin rate limiter uses hashed IPs. No IP column in login display. |
| **Traffic analysis** | Full Tor onion service with ephemeral key persistence. |

## Features

- **100% Anonymous** — No IPs, no logs, no Server header, no PII
- **Tor-native** — Auto-start `.onion` hidden service when `tor` is installed
- **Terminal UI** — Fast, keyboard-driven client built with [Textual](https://textual.textualize.io)
- **Self-hosted** — Your data, your server. No cloud, no surveillance
- **Encrypted DMs** — End-to-end encrypted direct messages using PGP
- **Admin panel** — Web-based admin at `:8001` with dashboard, user management, IP ban, audit
- **Proof-of-Work** — Hashcash-style PoW + math challenge to prevent bot registration
- **i18n** — Multi-language interface (Portuguese, English)
- **Minimalist** — No JavaScript frontend. Just a Python server + TUI client

## Quick start

### Requirements

- Python 3.11+
- Tor (optional, for `.onion` — **recommended for anonymity**)

### Install

**Linux / macOS** (no Python needed):
```bash
curl -fsSL https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/install.sh | bash
```

**Windows** (no Python needed):
```batch
curl -fsSLo install.bat https://raw.githubusercontent.com/MCookinho/hexfeed/main/scripts/install.bat
install.bat
```

**Arch Linux**:
```bash
yay -S hexfeed
```

**From source** (needs Python 3.11+):
```bash
git clone https://github.com/MCookinho/hexfeed.git
cd hexfeed
python scripts/install.py
```

The installer handles everything: Python detection, Tor installation, virtual environment, dependencies, and PATH setup.

```bash
# Options
python scripts/install.py -y              # non-interactive
python scripts/install.py --no-tor        # skip Tor
python scripts/install.py --dir ~/hex     # custom directory
```

### Start

```bash
# Start the server (localhost only + Tor if available)
hexfeed-server

# In another terminal, start the client
hexfeed
```

On first run, Tor auto-starts if the `tor` binary is available:
```
  ⬡ hexfeed:         http://127.0.0.1:8000
  🧅 Starting Tor (may take up to 2 min on first run)...
  🧅 Onion service active: http://abcdef1234567890.onion
  🧅 Share this address for external access — no real IP is exposed.
```

### First-time setup

1. Register an account from the TUI client
2. Access the admin panel at **http://127.0.0.1:8001/admin**
3. Set your admin password on first access
4. That's it. You're running your own anonymous social network.

### Share your onion address

```bash
curl http://127.0.0.1:8000/api/onion
# → {"onion_address": "abcdef1234567890.onion"}
```

Share this address with friends. They connect via Tor — **no one sees your real IP**.

## Architecture

```
Internet ──> Tor Network ──> .onion ──> 127.0.0.1:8000 ──> FastAPI ──> SQLite
Local    ──> 127.0.0.1:8000 (same machine)
LAN      ──> ❌ Blocked by design
```

```
┌──────────────┐     HTTP/JSON      ┌──────────────┐
│  TUI Client   │ ────────────────>  │  FastAPI      │
│  (Textual)    │ <────────────────  │  Server       │
└──────────────┘                    └───────┬──────┘
                                            │
                                   ┌────────┴────────┐
                                   │  SQLite3         │
                                   │  (no IP storage) │
                                   └─────────────────┘
```

## API

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/register` | Create account (PoW + math challenge required) |
| POST | `/login` | Authenticate |
| GET | `/feed` | Get timeline |
| POST | `/posts` | Create post |
| GET | `/posts/{id}` | Get post |
| GET | `/users/{username}` | User profile |
| POST | `/dm/send` | Send encrypted DM |
| GET | `/dm/inbox` | Read DMs |
| GET | `/api/onion` | Get server's `.onion` address |

## Platform installation

### Arch Linux (AUR) — recommended
```bash
yay -S hexfeed
```

### Linux (any distro)
```bash
bash scripts/install.sh
```

### macOS
```bash
bash scripts/install-macos.sh
```

### Windows
```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest
hexfeed-server --reload
```

## License

MIT License. See [LICENSE](LICENSE).

---

> Built with Python, [Textual](https://textual.textualize.io), and [FastAPI](https://fastapi.tiangolo.com).
> **Anonymity is not a feature. It's a requirement.**
