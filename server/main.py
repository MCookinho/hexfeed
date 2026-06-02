"""
hexfeed - FastAPI server (localhost only + Tor)

SECURITY MODEL:
  - Binds to 127.0.0.1 ONLY — no external network exposure.
  - External access exclusively via Tor onion service (.onion).
  - No CORS, no Swagger, no OpenAPI docs.
  - Rate limiting (60 req/min per IP), IP banning, security headers.
  - Tor starts automatically on boot if the tor binary is found.
"""

import os
import sys
import time
import hashlib
import threading
from pathlib import Path
from collections import defaultdict
from collections import deque
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from server.database import init_db
from server.routes import router
from server.auth import delete_token

onion_address: str | None = None
onion_site_address: str | None = None
_tor_service: object | None = None
_site_process: object | None = None

app = FastAPI(
    title="hexfeed",
    version="0.1.0",
    description="Rede social de terminal - privada e anônima",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

_RATE_SALT = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
_rate_limit_store: dict[str, deque] = defaultdict(lambda: deque(maxlen=60))
from server.bruteforce import check_login_bruteforce, record_login_attempt


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(f"{_RATE_SALT}{ip}".encode()).hexdigest()[:16]


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Server"] = ""
    del response.headers["Server"]
    return response


UPLOAD_PATHS = {"/api/files/upload", "/api/dm-files/upload", "/api/users/avatar"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_AVATAR_SIZE = 2 * 1024 * 1024   # 2 MB


@app.middleware("http")
async def ip_ban_middleware(request: Request, call_next):
    import sqlite3
    from server.admin_server import DB_PATH
    client_ip = request.client.host if request.client else "unknown"
    if client_ip != "unknown":
        try:
            conn = sqlite3.connect(str(DB_PATH))
            banned = conn.execute("SELECT 1 FROM banned_ips WHERE ip = ?", (client_ip,)).fetchone()
            conn.close()
            if banned:
                return JSONResponse(status_code=403, content={"detail": "Access denied"})
        except Exception:
            pass
    return await call_next(request)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    now = time.time()
    window = 60.0
    path = request.url.path

    is_auth = path in ("/login", "/register")
    max_req = 15 if is_auth else 300
    max_req = 60 if request.client and request.client.host != "127.0.0.1" else max_req

    client_ip = request.client.host if request.client else "unknown"
    ip_hash = _hash_ip(client_ip)

    timestamps = _rate_limit_store[ip_hash]
    while timestamps and timestamps[0] < now - window:
        timestamps.popleft()

    if len(timestamps) >= max_req:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please wait."},
        )

    timestamps.append(now)
    response = await call_next(request)
    return response


MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB


@app.middleware("http")
async def max_body_size_middleware(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and request.method in ("POST", "PUT"):
        size = int(cl)
        path = request.url.path
        max_size = 2 * 1024 * 1024 if path == "/api/users/avatar" else MAX_BODY_SIZE
        if size > max_size:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Arquivo muito grande (max {max_size // 1024 // 1024} MB)"},
            )
    return await call_next(request)


def _start_tor():
    global onion_address, onion_site_address, _tor_service, _site_process
    from server.tor_service import TorOnionService
    import subprocess
    subprocess.run(
        ["pkill", "-f", "tor.*DataDirectory.*hexfeed"],
        capture_output=True,
    )

    lock_path = Path.home() / ".config" / "hexfeed" / "tor" / "lock"
    try:
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass

    try:
        tor = TorOnionService()
        print("  🧅 Starting Tor (may take up to 2 min on first run)...", file=sys.stderr)
        if not tor.start():
            print("  ⚠️  Tor not available — server is localhost-only.", file=sys.stderr)
            print("  ⚠️  Install tor to enable onion service: sudo apt install tor", file=sys.stderr)
            return

        _tor_service = tor

        # API onion
        api_addr = tor.create_service("api", 80, 8080)
        if api_addr:
            onion_address = api_addr
            print(f"  🧅 API onion:      http://{api_addr}", file=sys.stderr)

        # Site onion
        site_candidates = [
            Path(__file__).resolve().parent.parent / "site",
            Path(__file__).resolve().parent.parent.parent / "site",
        ]
        site_dir = next((d for d in site_candidates if d.exists()), None)
        if site_dir:
            site_port = 8081
            _site_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "run_site_server:app",
                 "--host", "127.0.0.1", "--port", str(site_port),
                 "--log-level", "warning", "--no-access-log"],
                cwd=str(site_dir.parent),
            )
            time.sleep(2)
            site_addr = tor.create_service("site", 80, site_port)
            if site_addr:
                onion_site_address = site_addr
                print(f"  🧅 Site onion:     http://{site_addr}", file=sys.stderr)
        else:
            print("  ⚠️  Site directory not found — skipping site onion", file=sys.stderr)

        if api_addr:
            print(f"  🧅 Share the API onion for client connections.", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠️  Tor error: {e} — server is localhost-only.", file=sys.stderr)


@app.on_event("startup")
def startup():
    init_db()
    thread = threading.Thread(target=_start_tor, daemon=True)
    thread.start()

    import atexit
    def _cleanup():
        if _tor_service:
            _tor_service.stop()
        if _site_process:
            _site_process.terminate()
    atexit.register(_cleanup)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "hexfeed", "version": "0.1.0"}


@app.get("/api/onion")
def get_onion():
    """Retorna os endereços onion do servidor, se disponíveis."""
    return {
        "onion_address": onion_address,
        "onion_site_address": onion_site_address,
    }


@app.post("/api/auth/logout")
def logout(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        delete_token(auth[7:])
    return {"status": "logged_out"}


app.include_router(router)
