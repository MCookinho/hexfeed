# Hexfeed

> Private social network in your terminal. Minimalist, encrypted, Tor-ready.

hexfeed is a **self-hosted** social networking platform with a **terminal UI** client. No JavaScript, no trackers, no centralized servers — just you, your friends, and your terminal.

![](https://img.shields.io/badge/python-%3E%3D3.11-blue)
![](https://img.shields.io/badge/license-MIT-green)
![](https://img.shields.io/badge/status-beta-yellow)

---

## Features

- **Terminal UI** — Fast, keyboard-driven client built with [Textual](https://textual.textualize.io)
- **Self-hosted** — Your data, your server. No cloud, no surveillance
- **Tor support** — Optional `.onion` hidden service via [Stem](https://stem.torproject.org)
- **Encrypted DMs** — End-to-end encrypted direct messages using PGP
- **Admin panel** — Web-based admin at `:8001` with dashboard, user management, IP ban, audit logs, and more
- **i18n** — Multi-language interface (Portuguese, English)
- **Minimalist** — No JavaScript frontend. Just a Python server + TUI client
- **Private by design** — No telemetry, no ads, no tracking

## Quick start

### Requirements

- Python 3.11+
- pip
- Tor (optional, for `.onion`)

### Install

```bash
# Clone
git clone https://github.com/MCookinho/hexfeed.git
cd hexfeed

# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Or use the install script
bash scripts/install.sh
```

### Start

```bash
# Start the server (default: http://127.0.0.1:8000)
hexfeed-server

# In another terminal, start the client
hexfeed
```

### First-time setup

1. Register an account from the TUI client
2. Access the admin panel at **http://127.0.0.1:8001/admin**
3. Set your admin password on first access
4. That's it. You're running your own social network.

## Documentation

### Architecture

```
┌──────────────┐     HTTP/JSON      ┌──────────────┐
│  TUI Client   │ ────────────────>  │  FastAPI      │
│  (Textual)    │ <────────────────  │  Server       │
└──────────────┘                    └───────┬──────┘
                                            │
                                   ┌────────┴────────┐
                                   │  SQLite3         │
                                   │  (data/hexfeed.db)│
                                   └─────────────────┘
```

### File structure

```
hexfeed/
├── client/              # TUI client (Textual)
│   ├── app.py           # Main app
│   ├── screens/         # UI screens
│   ├── api.py           # HTTP client
│   └── i18n.py          # Internationalization
├── server/              # FastAPI server
│   ├── main.py          # Server entry
│   ├── routes.py        # API routes
│   ├── auth.py          # Authentication
│   ├── admin_server.py  # Admin panel
│   └── database.py      # SQLite layer
├── templates/           # Admin panel HTML
├── hexfeed/             # Pip entry points
├── scripts/             # Install scripts
├── tests/               # Test suite
└── pyproject.toml       # Package config
```

### API

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/register` | Create account |
| POST | `/login` | Authenticate |
| GET | `/feed` | Get timeline |
| POST | `/posts` | Create post |
| GET | `/posts/{id}` | Get post |
| GET | `/users/{username}` | User profile |
| POST | `/dm/send` | Send encrypted DM |
| GET | `/dm/inbox` | Read DMs |

### Security

- Passwords hashed with bcrypt
- DMs encrypted with PGP (per-recipient)
- Admin sessions with strict cookies + rate limiting
- File upload magic-byte validation
- Token expiry (30 days)
- IP banning via admin panel
- Security headers on all responses

## Platform installation

### Linux (any distro)
```bash
bash scripts/install.sh
```

### Arch Linux (AUR)
```bash
yay -S hexfeed
# or use the manual script:
bash scripts/install-arch.sh
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
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest

# Run with auto-reload
hexfeed-server --reload
```

## License

MIT License. See [LICENSE](LICENSE).

---

> Built with Python, [Textual](https://textual.textualize.io), and [FastAPI](https://fastapi.tiangolo.com).
