"""
hexfeed - Local Admin Panel
============================
Web dashboard for administrators, accessible only via localhost (127.0.0.1:8001).
Designed for MINIMAL exposure: no Swagger, no Redoc, no CORS,
no public routes beyond login.

Security isolation:
  - Runs in a separate process on port 8001
  - Exclusive bind to 127.0.0.1 (no external connections)
  - Authentication via bcrypt password + in-memory session
  - httponly cookie without expires (session)
  - No API routes exposed beyond login
  - Audit trail for all destructive actions
"""

import os
import sys
import json
import secrets
import sqlite3
import subprocess
import time
import socket
import shutil
import struct
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import deque

import bcrypt
import uuid
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "hexfeed.db"
ADMIN_PASSWORD_FILE = BASE_DIR / ".admin_password"
AUDIT_LOG_FILE = BASE_DIR / "data" / "admin_audit.json"
TEMPLATES_DIR = BASE_DIR / "templates" / "admin"
UPLOAD_DIR = BASE_DIR / "uploads"
DM_UPLOAD_DIR = BASE_DIR / "dm_uploads"
AVATAR_DIR = UPLOAD_DIR / "avatars"
SERVER_LOG_FILE = BASE_DIR / "server.log"

ADMIN_HOST = "127.0.0.1"
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "8001"))
SESSION_TTL = 86400

app = FastAPI(
    title="hexfeed Admin",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.cache = None
_sessions: dict[str, float] = {}
_session_created: dict[str, str] = {}
_admin_login_attempts: dict[str, list[float]] = {}
_ADMIN_RATE_SALT = hashlib.sha256(os.urandom(32)).hexdigest()[:16]


def _hash_admin_ip(ip: str) -> str:
    return hashlib.sha256(f"{_ADMIN_RATE_SALT}{ip}".encode()).hexdigest()[:16]


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_admin_tables(conn)
    return conn


def _ensure_admin_tables(conn: sqlite3.Connection):
    conn.execute("""CREATE TABLE IF NOT EXISTS banned_ips (
        ip TEXT PRIMARY KEY,
        reason TEXT DEFAULT '',
        banned_by TEXT DEFAULT 'admin',
        banned_at TEXT DEFAULT (datetime('now')),
        hit_count INTEGER DEFAULT 0
    )""")


def _load_audit_log() -> list[dict]:
    if not AUDIT_LOG_FILE.exists():
        return []
    try:
        data = json.loads(AUDIT_LOG_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _append_audit(action: str, details: dict):
    log = _load_audit_log()
    log.append({
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "details": details,
    })
    if len(log) > 500:
        log = log[-500:]
    AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG_FILE.write_text(json.dumps(log, indent=2, ensure_ascii=False))


def _ensure_admin_password() -> bool:
    if ADMIN_PASSWORD_FILE.exists():
        return True
    print("=" * 60)
    print("  \u2b21 hexfeed Admin \u2014 First-time setup")
    print("=" * 60)
    print("  Define the admin password (min. 8 characters):")
    env_pwd = os.environ.get("ADMIN_PASSWORD", "").strip()
    if env_pwd and len(env_pwd) >= 8:
        pwd_hash = bcrypt.hashpw(env_pwd.encode(), bcrypt.gensalt()).decode()
        ADMIN_PASSWORD_FILE.write_text(pwd_hash)
        print("  \u2705 Password configured via ADMIN_PASSWORD")
        return True
    while True:
        try:
            pwd = input("  Password: ").strip()
            if len(pwd) < 8:
                print("  \u274c Password must be at least 8 characters.")
                continue
            confirm = input("  Confirm: ").strip()
            if pwd != confirm:
                print("  \u274c Passwords do not match.")
                continue
            pwd_hash = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()
            ADMIN_PASSWORD_FILE.write_text(pwd_hash)
            print("  \u2705 Password configured successfully!")
            return True
        except (EOFError, KeyboardInterrupt):
            print("\n  \u274c Setup canceled.")
            return False


def _load_admin_hash() -> str:
    if ADMIN_PASSWORD_FILE.exists():
        return ADMIN_PASSWORD_FILE.read_text().strip()
    return ""


def _check_session(request: Request) -> bool:
    token = request.cookies.get("admin_token")
    if token and token in _sessions:
        if time.time() < _sessions[token]:
            return True
        del _sessions[token]
        _session_created.pop(token, None)
    return False


def _get_username(user_id: int, conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["username"] if row else f"deleted:{user_id}"


def _delete_user_physical_files(user_id: int, conn: sqlite3.Connection):
    rows = conn.execute("SELECT storage_name FROM files WHERE user_id = ?", (user_id,)).fetchall()
    for row in rows:
        fp = UPLOAD_DIR / row["storage_name"]
        if fp.exists():
            fp.unlink()
    rows = conn.execute("SELECT storage_name FROM dm_files WHERE user_id = ?", (user_id,)).fetchall()
    for row in rows:
        fp = DM_UPLOAD_DIR / row["storage_name"]
        if fp.exists():
            fp.unlink()
    u = conn.execute("SELECT avatar_path FROM users WHERE id = ?", (user_id,)).fetchone()
    if u and u["avatar_path"]:
        av = AVATAR_DIR / Path(u["avatar_path"]).name
        if av.exists():
            av.unlink()


def _delete_post_physical_file(post_id: int, conn: sqlite3.Connection):
    p = conn.execute("SELECT file_id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if p and p["file_id"]:
        f = conn.execute("SELECT storage_name, user_id FROM files WHERE id = ?", (p["file_id"],)).fetchone()
        if f:
            fp = UPLOAD_DIR / f["storage_name"]
            if fp.exists():
                fp.unlink()
            conn.execute("DELETE FROM files WHERE id = ?", (p["file_id"],))


def _fmt_size(bytes_val: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f}{unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f}TB"


def _fmt_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_timestamp(ts: str) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(ts)[:19]


def _get_setting(key: str, default: str = "") -> str:
    conn = _get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def _scan_audit_log(pattern: str) -> list[dict]:
    logs = _load_audit_log()
    if not pattern:
        return logs
    pattern_lower = pattern.lower()
    return [e for e in logs if pattern_lower in e.get("action", "").lower()]


TASK_STATUS_FILE = BASE_DIR / "data" / "task_status.json"


def _save_task_run(task_name: str):
    status = _load_task_status()
    status[task_name] = {"last_run": datetime.now().isoformat()}
    TASK_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TASK_STATUS_FILE.write_text(json.dumps(status, indent=2))


def _load_task_status() -> dict:
    if not TASK_STATUS_FILE.exists():
        return {}
    try:
        return json.loads(TASK_STATUS_FILE.read_text())
    except Exception:
        return {}


def _init_tables():
    conn = _get_db()
    try:
        conn.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN banned_at TIMESTAMP DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN banned_by TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    conn.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        active INTEGER DEFAULT 1
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        success INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS security_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        description TEXT DEFAULT '',
        username TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    defaults = [
        ("pow_difficulty", "20"),
        ("max_register_attempts", "15"),
        ("rate_window_hours", "1"),
        ("chat_cleanup_mins", "30"),
        ("max_post_length", "500"),
        ("max_avatar_size_mb", "2"),
    ]
    for k, v in defaults:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()


_init_tables()


@app.get("/")
async def root(request: Request):
    if _check_session(request):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _check_session(request):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"request": request})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    now = time.time()
    ip_hash = _hash_admin_ip(request.client.host if request.client else "unknown")
    attempts = _admin_login_attempts.setdefault(ip_hash, [])
    attempts = [t for t in attempts if t > now - 60]
    if len(attempts) >= 5:
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Too many attempts. Wait 60 seconds."}, status_code=429)
    stored_hash = _load_admin_hash()
    if not stored_hash:
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Admin not configured. Run --setup first."}, status_code=401)
    if bcrypt.checkpw(password.encode(), stored_hash.encode()):
        token = secrets.token_hex(32)
        _sessions[token] = time.time() + SESSION_TTL
        _session_created[token] = datetime.now().isoformat()
        resp = RedirectResponse("/dashboard", status_code=302)
        resp.set_cookie("admin_token", token, httponly=True, max_age=SESSION_TTL, path="/", samesite="strict")
        return resp
    attempts.append(now)
    _admin_login_attempts[ip_hash] = attempts
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Incorrect password."}, status_code=401)


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("admin_token")
    _sessions.pop(token, None)
    _session_created.pop(token, None)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("admin_token", path="/")
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    post_count = conn.execute("SELECT COUNT(*) as c FROM posts").fetchone()["c"]
    file_count = conn.execute("SELECT COUNT(*) as c FROM files").fetchone()["c"]
    dm_file_count = conn.execute("SELECT COUNT(*) as c FROM dm_files").fetchone()["c"]
    chat_count = conn.execute("SELECT COUNT(*) as c FROM chat_messages").fetchone()["c"]
    like_count = conn.execute("SELECT COUNT(*) as c FROM likes").fetchone()["c"]
    follow_count = conn.execute("SELECT COUNT(*) as c FROM follows").fetchone()["c"]
    conv_count = conn.execute("SELECT COUNT(*) as c FROM conversations").fetchone()["c"]
    dm_count = conn.execute("SELECT COUNT(*) as c FROM direct_messages").fetchone()["c"]

    total_storage = conn.execute("SELECT COALESCE(SUM(size), 0) as s FROM files").fetchone()["s"]
    total_dm_storage = conn.execute("SELECT COALESCE(SUM(size), 0) as s FROM dm_files").fetchone()["s"]

    recent_users = conn.execute(
        "SELECT id, username, display_name, created_at FROM users ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0

    audit_count = len(_load_audit_log())

    users_per_day = conn.execute(
        "SELECT DATE(created_at) as date, COUNT(*) as count FROM users WHERE created_at >= datetime('now', '-7 days') GROUP BY date ORDER BY date"
    ).fetchall()
    posts_per_day = conn.execute(
        "SELECT DATE(created_at) as date, COUNT(*) as count FROM posts WHERE created_at >= datetime('now', '-7 days') GROUP BY date ORDER BY date"
    ).fetchall()
    top_users = conn.execute(
        "SELECT u.username, u.id, COUNT(p.id) as post_count FROM users u LEFT JOIN posts p ON p.user_id = u.id GROUP BY u.id ORDER BY post_count DESC LIMIT 5"
    ).fetchall()
    new_users_today = conn.execute(
        "SELECT COUNT(*) as c FROM users WHERE DATE(created_at) = DATE('now')"
    ).fetchone()["c"]
    new_posts_today = conn.execute(
        "SELECT COUNT(*) as c FROM posts WHERE DATE(created_at) = DATE('now')"
    ).fetchone()["c"]

    conn.close()
    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "user_count": _fmt_number(user_count),
        "post_count": _fmt_number(post_count),
        "file_count": _fmt_number(file_count),
        "dm_file_count": _fmt_number(dm_file_count),
        "chat_count": _fmt_number(chat_count),
        "like_count": _fmt_number(like_count),
        "follow_count": _fmt_number(follow_count),
        "conv_count": _fmt_number(conv_count),
        "dm_count": _fmt_number(dm_count),
        "total_storage": _fmt_size(total_storage + total_dm_storage),
        "db_size": _fmt_size(db_size),
        "recent_users": recent_users,
        "audit_count": audit_count,
        "users_per_day": users_per_day,
        "posts_per_day": posts_per_day,
        "top_users": top_users,
        "new_users_today": new_users_today,
        "new_posts_today": new_posts_today,
    })


@app.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, q: str = "", page: int = 1):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    per_page = 30
    offset = (page - 1) * per_page
    conn = _get_db()

    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """SELECT u.*,
               (SELECT COUNT(*) FROM posts WHERE user_id = u.id) as post_count,
               (SELECT COUNT(*) FROM files WHERE user_id = u.id) as file_count,
               (SELECT COUNT(*) FROM follows WHERE following_id = u.id) as follower_count
               FROM users u
               WHERE u.username LIKE ? OR u.display_name LIKE ? OR u.email LIKE ?
               ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
            (like, like, like, per_page, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as c FROM users WHERE username LIKE ? OR display_name LIKE ? OR email LIKE ?",
            (like, like, like),
        ).fetchone()["c"]
    else:
        rows = conn.execute(
            """SELECT u.*,
               (SELECT COUNT(*) FROM posts WHERE user_id = u.id) as post_count,
               (SELECT COUNT(*) FROM files WHERE user_id = u.id) as file_count,
               (SELECT COUNT(*) FROM follows WHERE following_id = u.id) as follower_count
               FROM users u ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]

    conn.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(request, "users.html", {
        "request": request,
        "users": rows,
        "q": q,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@app.post("/users/{user_id}/delete")
async def delete_user(user_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "Usu\u00e1rio n\u00e3o encontrado")
    username = user["username"]
    _delete_user_physical_files(user_id, conn)
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    _append_audit("delete_user", {"user_id": user_id, "username": username})
    return RedirectResponse("/users", status_code=302)


@app.get("/posts", response_class=HTMLResponse)
async def posts_list(request: Request, q: str = "", page: int = 1):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    per_page = 30
    offset = (page - 1) * per_page
    conn = _get_db()

    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """SELECT p.*, u.username, u.display_name
               FROM posts p JOIN users u ON p.user_id = u.id
               WHERE p.content LIKE ?
               ORDER BY p.created_at DESC LIMIT ? OFFSET ?""",
            (like, per_page, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as c FROM posts WHERE content LIKE ?", (like,)
        ).fetchone()["c"]
    else:
        rows = conn.execute(
            """SELECT p.*, u.username, u.display_name
               FROM posts p JOIN users u ON p.user_id = u.id
               ORDER BY p.created_at DESC LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) as c FROM posts").fetchone()["c"]

    conn.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(request, "posts.html", {
        "request": request,
        "posts": rows,
        "q": q,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@app.post("/posts/{post_id}/delete")
async def delete_post(post_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    post = conn.execute(
        "SELECT p.id, p.content, u.username FROM posts p JOIN users u ON p.user_id = u.id WHERE p.id = ?",
        (post_id,),
    ).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, "Post n\u00e3o encontrado")
    _delete_post_physical_file(post_id, conn)
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    _append_audit("delete_post", {"post_id": post_id, "author": post["username"], "content_preview": post["content"][:80]})
    return RedirectResponse("/posts", status_code=302)


@app.get("/files", response_class=HTMLResponse)
async def files_list(request: Request, q: str = "", page: int = 1):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    per_page = 30
    offset = (page - 1) * per_page
    conn = _get_db()

    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """SELECT f.*, u.username
               FROM files f JOIN users u ON f.user_id = u.id
               WHERE f.original_name LIKE ? OR u.username LIKE ?
               ORDER BY f.uploaded_at DESC LIMIT ? OFFSET ?""",
            (like, like, per_page, offset),
        ).fetchall()
        total = conn.execute(
            """SELECT COUNT(*) as c FROM files f JOIN users u ON f.user_id = u.id
               WHERE f.original_name LIKE ? OR u.username LIKE ?""",
            (like, like),
        ).fetchone()["c"]
    else:
        rows = conn.execute(
            """SELECT f.*, u.username
               FROM files f JOIN users u ON f.user_id = u.id
               ORDER BY f.uploaded_at DESC LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) as c FROM files").fetchone()["c"]

    conn.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(request, "files.html", {
        "request": request,
        "files": rows,
        "q": q,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@app.post("/files/{file_id}/delete")
async def delete_file(file_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    f = conn.execute(
        "SELECT f.*, u.username FROM files f JOIN users u ON f.user_id = u.id WHERE f.id = ?",
        (file_id,),
    ).fetchone()
    if not f:
        conn.close()
        raise HTTPException(404, "File not found")
    fp = UPLOAD_DIR / f["storage_name"]
    if fp.exists():
        fp.unlink()
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    conn.commit()
    conn.close()
    _append_audit("delete_file", {"file_id": file_id, "name": f["original_name"], "owner": f["username"]})
    return RedirectResponse("/files", status_code=302)


@app.get("/audit", response_class=HTMLResponse)
async def audit_log(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    logs = _load_audit_log()
    logs.reverse()
    return templates.TemplateResponse(request, "audit.html", {"request": request, "logs": logs})


@app.post("/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    stored_hash = _load_admin_hash()
    if not stored_hash:
        return templates.TemplateResponse(request, "audit.html", {
            "request": request,
            "logs": _load_audit_log(),
            "error": "Admin not configured.",
        })
    if not bcrypt.checkpw(current_password.encode(), stored_hash.encode()):
        return templates.TemplateResponse(request, "audit.html", {
            "request": request,
            "logs": _load_audit_log(),
            "error": "Current password is incorrect.",
        })
    if len(new_password) < 8:
        return templates.TemplateResponse(request, "audit.html", {
            "request": request,
            "logs": _load_audit_log(),
            "error": "New password must be at least 8 characters.",
        })
    if new_password != confirm_password:
        return templates.TemplateResponse(request, "audit.html", {
            "request": request,
            "logs": _load_audit_log(),
            "error": "Passwords do not match.",
        })
    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    ADMIN_PASSWORD_FILE.write_text(new_hash)
    _sessions.clear()
    _session_created.clear()
    _append_audit("change_password", {"timestamp": datetime.now().isoformat()})
    return templates.TemplateResponse(request, "audit.html", {
        "request": request,
        "logs": _load_audit_log(),
        "success": "Password changed successfully!",
    })


@app.post("/db/maintenance")
async def db_maintenance(request: Request):
    if not _check_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = _get_db()
    conn.execute("VACUUM")
    conn.execute("PRAGMA integrity_check")
    conn.close()
    _append_audit("db_maintenance", {"action": "VACUUM + integrity_check"})
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: int):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "Usu\u00e1rio n\u00e3o encontrado")

    posts = conn.execute(
        "SELECT id, content, created_at FROM posts WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (user_id,),
    ).fetchall()

    files = conn.execute(
        "SELECT id, original_name, size, uploaded_at FROM files WHERE user_id = ? ORDER BY uploaded_at DESC LIMIT 20",
        (user_id,),
    ).fetchall()

    conversations = conn.execute(
        """SELECT c.id, c.user1_id, c.user2_id, c.created_at,
                  u1.username as user1_name, u2.username as user2_name
           FROM conversations c
           JOIN users u1 ON c.user1_id = u1.id
           JOIN users u2 ON c.user2_id = u2.id
           WHERE c.user1_id = ? OR c.user2_id = ?
           ORDER BY c.updated_at DESC LIMIT 20""",
        (user_id, user_id),
    ).fetchall()

    follower_count = conn.execute(
        "SELECT COUNT(*) as c FROM follows WHERE following_id = ?", (user_id,)
    ).fetchone()["c"]

    following_count = conn.execute(
        "SELECT COUNT(*) as c FROM follows WHERE follower_id = ?", (user_id,)
    ).fetchone()["c"]

    conn.close()

    return templates.TemplateResponse(request, "user_detail.html", {
        "request": request,
        "user": user,
        "posts": posts,
        "files": files,
        "conversations": conversations,
        "follower_count": follower_count,
        "following_count": following_count,
    })


@app.post("/users/{user_id}/ban")
async def ban_user(user_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "Usu\u00e1rio n\u00e3o encontrado")
    conn.execute(
        "UPDATE users SET banned = 1, banned_at = ?, banned_by = ? WHERE id = ?",
        (datetime.now().isoformat(), "admin", user_id),
    )
    conn.commit()
    conn.close()
    _append_audit("ban_user", {"user_id": user_id, "username": user["username"]})
    return RedirectResponse(f"/users/{user_id}", status_code=302)


@app.post("/users/{user_id}/unban")
async def unban_user(user_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "Usu\u00e1rio n\u00e3o encontrado")
    conn.execute(
        "UPDATE users SET banned = 0, banned_at = NULL, banned_by = '' WHERE id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()
    _append_audit("unban_user", {"user_id": user_id, "username": user["username"]})
    return RedirectResponse(f"/users/{user_id}", status_code=302)


@app.post("/users/{user_id}/reset-password")
async def reset_password(user_id: int, request: Request, new_password: str = Form(...)):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    if len(new_password) < 8:
        conn = _get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        posts = conn.execute(
            "SELECT id, content, created_at FROM posts WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (user_id,),
        ).fetchall()
        files = conn.execute(
            "SELECT id, original_name, size, uploaded_at FROM files WHERE user_id = ? ORDER BY uploaded_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        conversations = conn.execute(
            """SELECT c.id, c.user1_id, c.user2_id, c.created_at,
                      u1.username as user1_name, u2.username as user2_name
               FROM conversations c
               JOIN users u1 ON c.user1_id = u1.id
               JOIN users u2 ON c.user2_id = u2.id
               WHERE c.user1_id = ? OR c.user2_id = ?
               ORDER BY c.updated_at DESC LIMIT 20""",
            (user_id, user_id),
        ).fetchall()
        follower_count = conn.execute("SELECT COUNT(*) as c FROM follows WHERE following_id = ?", (user_id,)).fetchone()["c"]
        following_count = conn.execute("SELECT COUNT(*) as c FROM follows WHERE follower_id = ?", (user_id,)).fetchone()["c"]
        conn.close()
        return templates.TemplateResponse(request, "user_detail.html", {
            "request": request,
            "user": user,
            "posts": posts,
            "files": files,
            "conversations": conversations,
            "follower_count": follower_count,
            "following_count": following_count,
            "error": "Password must be at least 8 characters.",
        })
    conn = _get_db()
    user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "User not found")
    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
    conn.commit()
    conn.close()
    _append_audit("reset_password", {"user_id": user_id, "username": user["username"]})
    return RedirectResponse(f"/users/{user_id}", status_code=302)


@app.post("/users/{user_id}/delete-pgp")
async def delete_pgp(user_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "Usu\u00e1rio n\u00e3o encontrado")
    conn.execute(
        "UPDATE users SET pgp_public_key = '', pgp_fingerprint = '', pgp_private_key_hash = '' WHERE id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()
    _append_audit("delete_pgp", {"user_id": user_id, "username": user["username"]})
    return RedirectResponse(f"/users/{user_id}", status_code=302)


@app.get("/users/{user_id}/export")
async def export_user_data(user_id: int, request: Request):
    if not _check_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = _get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "Usu\u00e1rio n\u00e3o encontrado")

    profile = dict(user)
    profile.pop("password_hash", None)
    profile.pop("pgp_private_key_hash", None)

    posts_rows = conn.execute(
        "SELECT id, content, reply_to, created_at, edited_at, file_id FROM posts WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()

    files_rows = conn.execute(
        "SELECT id, original_name, size, content_type, uploaded_at FROM files WHERE user_id = ? ORDER BY uploaded_at DESC",
        (user_id,),
    ).fetchall()

    dms = conn.execute(
        """SELECT dm.id, dm.content, dm.created_at, dm.file_id, dm.dm_file_id,
                  c.id as conversation_id, u.username as other_user
           FROM direct_messages dm
           JOIN conversations c ON dm.conversation_id = c.id
           JOIN users u ON (CASE WHEN c.user1_id = ? THEN c.user2_id ELSE c.user1_id END) = u.id
           WHERE c.user1_id = ? OR c.user2_id = ?
           ORDER BY dm.created_at DESC LIMIT 500""",
        (user_id, user_id, user_id),
    ).fetchall()

    chats = conn.execute(
        "SELECT id, content, is_anonymous, created_at FROM chat_messages WHERE user_id = ? ORDER BY created_at DESC LIMIT 500",
        (user_id,),
    ).fetchall()

    likes = conn.execute(
        "SELECT post_id, created_at FROM likes WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()

    followers = conn.execute(
        "SELECT u.username, f.created_at FROM follows f JOIN users u ON f.follower_id = u.id WHERE f.following_id = ? ORDER BY f.created_at DESC",
        (user_id,),
    ).fetchall()

    following = conn.execute(
        "SELECT u.username, f.created_at FROM follows f JOIN users u ON f.following_id = u.id WHERE f.follower_id = ? ORDER BY f.created_at DESC",
        (user_id,),
    ).fetchall()

    conn.close()

    data = {
        "profile": profile,
        "posts": [dict(r) for r in posts_rows],
        "files": [dict(r) for r in files_rows],
        "direct_messages": [dict(r) for r in dms],
        "chat_messages": [dict(r) for r in chats],
        "likes": [dict(r) for r in likes],
        "followers": [dict(r) for r in followers],
        "following": [dict(r) for r in following],
        "exported_at": datetime.now().isoformat(),
    }

    json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    return StreamingResponse(
        iter([json_bytes]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="user_{user_id}_export.json"',
            "Content-Length": str(len(json_bytes)),
        },
    )


@app.get("/chat", response_class=HTMLResponse)
async def chat_messages(request: Request, q: str = "", page: int = 1, groups_page: int = 1, groups_per_page: int = 20):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    per_page = 50
    offset = (page - 1) * per_page
    conn = _get_db()

    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """SELECT m.*, u.username
               FROM chat_messages m JOIN users u ON m.user_id = u.id
               WHERE u.username LIKE ? OR m.content LIKE ?
               ORDER BY m.created_at DESC LIMIT ? OFFSET ?""",
            (like, like, per_page, offset),
        ).fetchall()
        total = conn.execute(
            """SELECT COUNT(*) as c FROM chat_messages m JOIN users u ON m.user_id = u.id
               WHERE u.username LIKE ? OR m.content LIKE ?""",
            (like, like),
        ).fetchone()["c"]
    else:
        rows = conn.execute(
            """SELECT m.*, u.username
               FROM chat_messages m JOIN users u ON m.user_id = u.id
               ORDER BY m.created_at DESC LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) as c FROM chat_messages").fetchone()["c"]

    groups_offset = (groups_page - 1) * groups_per_page
    groups = conn.execute(
        """SELECT g.*, COUNT(gm.user_id) as member_count
           FROM groups g LEFT JOIN group_members gm ON gm.group_id = g.id
           GROUP BY g.id ORDER BY g.created_at DESC LIMIT ? OFFSET ?""",
        (groups_per_page, groups_offset),
    ).fetchall()
    group_total = conn.execute("SELECT COUNT(*) as c FROM groups").fetchone()["c"]
    group_total_pages = max(1, (group_total + groups_per_page - 1) // groups_per_page)

    conn.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(request, "chat.html", {
        "request": request,
        "messages": rows,
        "q": q,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "groups": groups,
        "groups_page": groups_page,
        "groups_per_page": groups_per_page,
        "group_total": group_total,
        "group_total_pages": group_total_pages,
    })


@app.post("/chat/{message_id}/delete")
async def delete_chat_message(message_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    msg = conn.execute(
        "SELECT m.id, m.content, u.username FROM chat_messages m JOIN users u ON m.user_id = u.id WHERE m.id = ?",
        (message_id,),
    ).fetchone()
    if not msg:
        conn.close()
        raise HTTPException(404, "Message not found")
    conn.execute("DELETE FROM chat_messages WHERE id = ?", (message_id,))
    conn.commit()
    conn.close()
    _append_audit("delete_chat_message", {"message_id": message_id, "author": msg["username"], "content_preview": msg["content"][:80]})
    return RedirectResponse("/chat", status_code=302)


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", page: int = 1):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    if not q:
        return templates.TemplateResponse(request, "search.html", {
            "request": request,
            "q": q,
            "users": [],
            "posts": [],
            "files": [],
            "page": 1,
            "total_pages": 1,
        })
    per_page = 30
    offset = (page - 1) * per_page
    like = f"%{q}%"
    conn = _get_db()

    users = conn.execute(
        "SELECT id, username, display_name, created_at FROM users WHERE username LIKE ? OR display_name LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (like, like, per_page, offset),
    ).fetchall()
    user_total = conn.execute(
        "SELECT COUNT(*) as c FROM users WHERE username LIKE ? OR display_name LIKE ?",
        (like, like),
    ).fetchone()["c"]

    posts = conn.execute(
        """SELECT p.id, p.content, p.created_at, p.user_id, u.username
           FROM posts p JOIN users u ON p.user_id = u.id
           WHERE p.content LIKE ?
           ORDER BY p.created_at DESC LIMIT ? OFFSET ?""",
        (like, per_page, offset),
    ).fetchall()
    post_total = conn.execute(
        "SELECT COUNT(*) as c FROM posts WHERE content LIKE ?", (like,)
    ).fetchone()["c"]

    files = conn.execute(
        """SELECT f.id, f.original_name, f.size, f.uploaded_at, f.user_id, u.username as owner
           FROM files f JOIN users u ON f.user_id = u.id
           WHERE f.original_name LIKE ?
           ORDER BY f.uploaded_at DESC LIMIT ? OFFSET ?""",
        (like, per_page, offset),
    ).fetchall()
    file_total = conn.execute(
        "SELECT COUNT(*) as c FROM files WHERE original_name LIKE ?", (like,)
    ).fetchone()["c"]

    conn.close()

    max_total = max(user_total, post_total, file_total)
    total_pages = max(1, (max_total + per_page - 1) // per_page)

    return templates.TemplateResponse(request, "search.html", {
        "request": request,
        "q": q,
        "users": users,
        "posts": posts,
        "files": files,
        "page": page,
        "total_pages": total_pages,
    })


@app.post("/bulk/delete-posts")
async def bulk_delete_posts(request: Request):
    if not _check_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inv\u00e1lido")
    user_id = body.get("user_id")
    before_date = body.get("before_date")
    query = body.get("query")

    conn = _get_db()
    conditions = []
    params = []

    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    if before_date:
        conditions.append("created_at < ?")
        params.append(before_date)
    if query:
        conditions.append("content LIKE ?")
        params.append(f"%{query}%")

    if not conditions:
        conn.close()
        return JSONResponse({"error": "Nenhum filtro fornecido"}, status_code=400)

    where = " AND ".join(conditions)
    rows = conn.execute(f"SELECT id FROM posts WHERE {where}", params).fetchall()
    ids = [r["id"] for r in rows]

    for pid in ids:
        _delete_post_physical_file(pid, conn)

    deleted = conn.execute(f"DELETE FROM posts WHERE {where}", params).rowcount
    conn.commit()
    conn.close()

    _append_audit("bulk_delete_posts", {"filter": body, "deleted_count": deleted})
    return JSONResponse({"deleted_count": deleted})


@app.post("/bulk/delete-files")
async def bulk_delete_files(request: Request):
    if not _check_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inv\u00e1lido")
    user_id = body.get("user_id")
    before_date = body.get("before_date")
    query = body.get("query")

    conn = _get_db()
    conditions = []
    params = []

    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    if before_date:
        conditions.append("uploaded_at < ?")
        params.append(before_date)
    if query:
        conditions.append("original_name LIKE ?")
        params.append(f"%{query}%")

    if not conditions:
        conn.close()
        return JSONResponse({"error": "No filter provided"}, status_code=400)

    where = " AND ".join(conditions)

    rows = conn.execute(f"SELECT id, storage_name FROM files WHERE {where}", params).fetchall()
    for r in rows:
        fp = UPLOAD_DIR / r["storage_name"]
        if fp.exists():
            fp.unlink()

    deleted = conn.execute(f"DELETE FROM files WHERE {where}", params).rowcount
    conn.commit()
    conn.close()

    _append_audit("bulk_delete_files", {"filter": body, "deleted_count": deleted})
    return JSONResponse({"deleted_count": deleted})


@app.get("/system", response_class=HTMLResponse)
async def system_settings(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    settings_rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings_dict = {r["key"]: r["value"] for r in settings_rows}

    announcements = conn.execute(
        "SELECT * FROM announcements ORDER BY created_at DESC"
    ).fetchall()

    db_size = _fmt_size(DB_PATH.stat().st_size) if DB_PATH.exists() else "0B"

    try:
        usage = shutil.disk_usage(BASE_DIR)
        disk_free = _fmt_size(usage.free)
    except Exception:
        disk_free = "N/A"

    user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    post_count = conn.execute("SELECT COUNT(*) as c FROM posts").fetchone()["c"]
    conn.close()

    task_status = _load_task_status()
    task_defs = [
        {"name": "token_cleanup", "label": "Token Cleanup", "status": "active"},
        {"name": "chat_cleanup", "label": "Chat Cleanup", "status": "active"},
        {"name": "orphaned_files", "label": "Orphaned File Cleanup", "status": "manual"},
        {"name": "db_vacuum", "label": "Database VACUUM", "status": "manual"},
    ]
    tasks = []
    for td in task_defs:
        last_run = task_status.get(td["name"], {}).get("last_run", None)
        if last_run:
            last_run_fmt = _fmt_timestamp(last_run)
        else:
            last_run_fmt = "Never"
        if td["status"] == "manual":
            next_run = "Manual"
        else:
            next_run = "Scheduled"
        tasks.append({
            "name": td["name"],
            "label": td["label"],
            "last_run": last_run_fmt,
            "next_run": next_run,
            "status": td["status"],
        })

    db_size_formatted = db_size
    active_connections = 0
    slow_queries = list(_slow_query_log)

    return templates.TemplateResponse(request, "system.html", {
        "request": request,
        "settings": settings_dict,
        "announcements": announcements,
        "db_size": db_size,
        "disk_free": disk_free,
        "user_count": _fmt_number(user_count),
        "post_count": _fmt_number(post_count),
        "tasks": tasks,
        "db_size_formatted": db_size_formatted,
        "active_connections": active_connections,
        "slow_queries": slow_queries,
    })


@app.post("/system/save")
async def save_settings(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    form = await request.form()
    conn = _get_db()
    for key, value in form.items():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()
    _append_audit("save_settings", {"keys": list(form.keys())})
    return RedirectResponse("/system", status_code=302)


@app.get("/system/health")
async def system_health(request: Request):
    if not _check_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = _get_db()
    user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    post_count = conn.execute("SELECT COUNT(*) as c FROM posts").fetchone()["c"]
    conn.close()

    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    try:
        usage = shutil.disk_usage(BASE_DIR)
        disk_free = usage.free
    except Exception:
        disk_free = 0

    uptime_seconds = int(time.time() - _app_start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    return JSONResponse({
        "status": "ok",
        "db_size_bytes": db_size,
        "db_size_human": _fmt_size(db_size),
        "disk_free_bytes": disk_free,
        "disk_free_human": _fmt_size(disk_free),
        "user_count": user_count,
        "post_count": post_count,
        "uptime_seconds": uptime_seconds,
        "uptime": uptime_str,
        "timestamp": datetime.now().isoformat(),
    })


@app.get("/system/backup")
async def db_backup(request: Request):
    if not _check_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not DB_PATH.exists():
        raise HTTPException(404, "Banco de dados n\u00e3o encontrado")
    return FileResponse(
        path=str(DB_PATH),
        media_type="application/octet-stream",
        filename="hexfeed_backup.db",
        headers={
            "Content-Disposition": f'attachment; filename="hexfeed_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db"',
        },
    )


@app.get("/announcements", response_class=HTMLResponse)
async def announcements_list(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    announcements = conn.execute(
        "SELECT * FROM announcements ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(request, "system.html", {
        "request": request,
        "announcements": announcements,
        "settings": {},
        "db_size": _fmt_size(DB_PATH.stat().st_size) if DB_PATH.exists() else "0B",
        "disk_free": "N/A",
        "user_count": "0",
        "post_count": "0",
    })


@app.post("/announcements")
async def create_announcement(request: Request, content: str = Form(...)):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    if not content.strip():
        conn = _get_db()
        announcements = conn.execute("SELECT * FROM announcements ORDER BY created_at DESC").fetchall()
        conn.close()
        return templates.TemplateResponse(request, "system.html", {
            "request": request,
            "announcements": announcements,
            "settings": {},
            "db_size": _fmt_size(DB_PATH.stat().st_size) if DB_PATH.exists() else "0B",
            "disk_free": "N/A",
            "user_count": "0",
            "post_count": "0",
            "error": "O conte\u00fado do an\u00fancio n\u00e3o pode estar vazio.",
        })
    conn = _get_db()
    conn.execute(
        "INSERT INTO announcements (content, created_by) VALUES (?, ?)",
        (content.strip(), "admin"),
    )
    conn.commit()
    conn.close()
    _append_audit("create_announcement", {"content_preview": content[:80]})
    return RedirectResponse("/system", status_code=302)


@app.post("/announcements/{ann_id}/toggle")
async def toggle_announcement(ann_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    ann = conn.execute("SELECT id, active FROM announcements WHERE id = ?", (ann_id,)).fetchone()
    if not ann:
        conn.close()
        raise HTTPException(404, "An\u00fancio n\u00e3o encontrado")
    new_active = 0 if ann["active"] else 1
    conn.execute("UPDATE announcements SET active = ? WHERE id = ?", (new_active, ann_id))
    conn.commit()
    conn.close()
    _append_audit("toggle_announcement", {"announcement_id": ann_id, "new_active": new_active})
    return RedirectResponse("/system", status_code=302)


@app.get("/logs", response_class=HTMLResponse)
async def system_logs(request: Request, refresh: str = ""):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)

    log_lines = []

    if SERVER_LOG_FILE.exists():
        try:
            text = SERVER_LOG_FILE.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            log_lines = lines[-500:]
        except Exception:
            log_lines = ["(Error reading log file)"]
    else:
        log_lines = _load_audit_log()
        log_lines.reverse()
        log_lines = log_lines[:500]

    return templates.TemplateResponse(request, "logs.html", {
        "request": request,
        "log_lines": log_lines,
        "auto_refresh": refresh == "1",
    })


@app.get("/network", response_class=HTMLResponse)
async def network_page(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)

    tor_online = False
    tor_version = ""

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex(("127.0.0.1", 9050))
        s.close()
        if result == 0:
            tor_online = True
    except Exception:
        pass

    if not tor_online:
        try:
            if Path("/var/run/tor/").exists():
                tor_online = True
        except Exception:
            pass

    try:
        result = subprocess.run(["tor", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            tor_version = result.stdout.strip().split("\n")[0]
    except Exception:
        tor_version = ""

    def _dir_size(path: Path) -> tuple[int, int]:
        total_size = 0
        count = 0
        if path.exists():
            for f in path.rglob("*"):
                if f.is_file():
                    try:
                        total_size += f.stat().st_size
                        count += 1
                    except Exception:
                        pass
        return total_size, count

    uploads_size, uploads_count = _dir_size(UPLOAD_DIR)
    dm_uploads_size, dm_uploads_count = _dir_size(DM_UPLOAD_DIR)
    avatars_size, avatars_count = _dir_size(AVATAR_DIR)

    total_storage = uploads_size + dm_uploads_size + avatars_size

    conn = _get_db()
    largest_files = conn.execute(
        """SELECT f.*, u.username as owner
           FROM files f JOIN users u ON f.user_id = u.id
           ORDER BY f.size DESC LIMIT 20"""
    ).fetchall()
    conn.close()

    return templates.TemplateResponse(request, "network.html", {
        "request": request,
        "tor_online": tor_online,
        "tor_version": tor_version,
        "uploads_size": _fmt_size(uploads_size),
        "uploads_count": uploads_count,
        "dm_uploads_size": _fmt_size(dm_uploads_size),
        "dm_uploads_count": dm_uploads_count,
        "avatars_size": _fmt_size(avatars_size),
        "avatars_count": avatars_count,
        "total_storage": _fmt_size(total_storage),
        "largest_files": largest_files,
    })


@app.post("/network/prune-orphaned")
async def prune_orphaned(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()

    known_storage = set()
    for row in conn.execute("SELECT storage_name FROM files").fetchall():
        known_storage.add(row["storage_name"])
    for row in conn.execute("SELECT storage_name FROM dm_files").fetchall():
        known_storage.add(row["storage_name"])

    pruned_count = 0

    for dir_path in [UPLOAD_DIR, DM_UPLOAD_DIR]:
        if not dir_path.exists():
            continue
        for f in dir_path.iterdir():
            if f.is_file() and f.name not in known_storage:
                if f.name == "avatars":
                    continue
                try:
                    f.unlink()
                    pruned_count += 1
                except Exception:
                    pass

    if AVATAR_DIR.exists():
        avatars_in_use = set()
        for row in conn.execute("SELECT avatar_path FROM users WHERE avatar_path != ''").fetchall():
            if row["avatar_path"]:
                avatars_in_use.add(Path(row["avatar_path"]).name)

    if AVATAR_DIR.exists():
        for f in AVATAR_DIR.iterdir():
            if f.name not in avatars_in_use:
                try:
                    f.unlink()
                except Exception:
                    pass

    conn.close()
    _append_audit("prune_orphaned", {"pruned_count": pruned_count})
    return RedirectResponse("/network", status_code=302)


@app.get("/security", response_class=HTMLResponse)
async def security_page(
    request: Request,
    q: str = "",
    la_page: int = 1,
    se_page: int = 1,
    ban_error: str = "",
    ban_success: str = "",
):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()

    banned_ips = conn.execute(
        "SELECT * FROM banned_ips ORDER BY banned_at DESC"
    ).fetchall()

    now = time.time()
    sessions_list = []
    for token, expires_at in list(_sessions.items()):
        if now < expires_at:
            created_ts = _session_created.get(token, "")
            expires_iso = datetime.fromtimestamp(expires_at).isoformat()
            sessions_list.append({
                "token": token[:8] + "...",
                "expires_at": expires_iso,
                "created_at": created_ts,
            })
    session_count = len(sessions_list)

    per_page = 50

    if q:
        like = f"%{q}%"
        login_attempts = conn.execute(
            "SELECT * FROM login_attempts WHERE username LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (like, per_page, (la_page - 1) * per_page),
        ).fetchall()
        la_total = conn.execute(
            "SELECT COUNT(*) as c FROM login_attempts WHERE username LIKE ?", (like,)
        ).fetchone()["c"]
    else:
        login_attempts = conn.execute(
            "SELECT * FROM login_attempts ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, (la_page - 1) * per_page),
        ).fetchall()
        la_total = conn.execute("SELECT COUNT(*) as c FROM login_attempts").fetchone()["c"]

    security_events = conn.execute(
        "SELECT * FROM security_events ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (per_page, (se_page - 1) * per_page),
    ).fetchall()
    se_total = conn.execute("SELECT COUNT(*) as c FROM security_events").fetchone()["c"]

    conn.close()

    la_total_pages = max(1, (la_total + per_page - 1) // per_page)
    se_total_pages = max(1, (se_total + per_page - 1) // per_page)

    return templates.TemplateResponse(request, "security.html", {
        "request": request,
        "session_count": session_count,
        "sessions": sessions_list,
        "q": q,
        "login_attempts": login_attempts,
        "la_page": la_page,
        "la_total_pages": la_total_pages,
        "security_events": security_events,
        "se_page": se_page,
        "se_total_pages": se_total_pages,
        "banned_ips": banned_ips,
        "ban_error": ban_error,
        "ban_success": ban_success,
    })


@app.post("/security/clear-login-attempts")
async def clear_login_attempts(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    conn.execute("DELETE FROM login_attempts")
    conn.commit()
    conn.close()
    _append_audit("clear_login_attempts", {})
    return RedirectResponse("/security", status_code=302)


@app.post("/security/clear-events")
async def clear_security_events(request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    conn.execute("DELETE FROM security_events")
    conn.commit()
    conn.close()
    _append_audit("clear_security_events", {})
    return RedirectResponse("/security", status_code=302)


@app.post("/security/ban-ip")
async def ban_ip(request: Request, ip: str = Form(...), reason: str = Form("")):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    conn.execute(
        "INSERT OR IGNORE INTO banned_ips (ip, reason, banned_by) VALUES (?, ?, 'admin')",
        (ip.strip(), reason.strip()),
    )
    conn.commit()
    conn.close()
    _append_audit("ban_ip", {"ip": ip.strip(), "reason": reason.strip()})
    return RedirectResponse("/security?ban_success=IP banned", status_code=302)


@app.post("/security/unban-ip/{ip}")
async def unban_ip(ip: str, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    conn.execute("DELETE FROM banned_ips WHERE ip = ?", (ip,))
    conn.commit()
    conn.close()
    _append_audit("unban_ip", {"ip": ip})
    return RedirectResponse("/security?ban_success=IP unbanned", status_code=302)


@app.get("/chat/groups/{group_id}/messages", response_class=HTMLResponse)
async def group_messages_list(request: Request, group_id: int):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not group:
        conn.close()
        raise HTTPException(404, "Group not found")
    messages = conn.execute(
        """SELECT gm.*, u.username, u.display_name
           FROM group_messages gm
           JOIN users u ON gm.user_id = u.id
           WHERE gm.group_id = ?
           ORDER BY gm.created_at DESC LIMIT 200""",
        (group_id,),
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(request, "chat.html", {
        "request": request,
        "group": group,
        "group_messages": messages,
    })


@app.post("/chat/groups/{group_id}/delete")
async def delete_group(group_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not group:
        conn.close()
        raise HTTPException(404, "Group not found")
    conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()
    _append_audit("delete_group", {"group_id": group_id})
    return RedirectResponse("/chat", status_code=302)


@app.post("/chat/groups/{group_id}/members/{user_id}/remove")
async def remove_group_member(group_id: int, user_id: int, request: Request):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    conn.execute("DELETE FROM group_members WHERE group_id = ? AND user_id = ?", (group_id, user_id))
    conn.commit()
    conn.close()
    _append_audit("remove_group_member", {"group_id": group_id, "user_id": user_id})
    return RedirectResponse("/chat", status_code=302)


@app.get("/users/{user_id}/preview")
async def user_preview(user_id: int, request: Request):
    if not _check_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = _get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(404, "User not found")
    posts = conn.execute(
        "SELECT id, content, created_at FROM posts WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
        (user_id,),
    ).fetchall()
    follower_count = conn.execute(
        "SELECT COUNT(*) as c FROM follows WHERE following_id = ?", (user_id,)
    ).fetchone()["c"]
    following_count = conn.execute(
        "SELECT COUNT(*) as c FROM follows WHERE follower_id = ?", (user_id,)
    ).fetchone()["c"]
    conn.close()
    profile = dict(user)
    profile.pop("password_hash", None)
    profile.pop("pgp_private_key_hash", None)
    return JSONResponse({
        "profile": profile,
        "posts": [dict(r) for r in posts],
        "follower_count": follower_count,
        "following_count": following_count,
    })


@app.post("/system/task/run/{task_name}")
async def run_task(request: Request, task_name: str):
    if not _check_session(request):
        return RedirectResponse("/login", status_code=302)
    conn = _get_db()
    if task_name == "token_cleanup":
        from server.auth import cleanup_expired_tokens
        cleanup_expired_tokens()
    elif task_name == "chat_cleanup":
        conn.execute("DELETE FROM chat_messages WHERE created_at < datetime('now', '-7 days')")
        conn.commit()
    elif task_name == "orphaned_files":
        known_storage = set()
        for row in conn.execute("SELECT storage_name FROM files").fetchall():
            known_storage.add(row["storage_name"])
        for row in conn.execute("SELECT storage_name FROM dm_files").fetchall():
            known_storage.add(row["storage_name"])
        for dir_path in [UPLOAD_DIR, DM_UPLOAD_DIR]:
            if not dir_path.exists():
                continue
            for f in dir_path.iterdir():
                if f.is_file() and f.name not in known_storage:
                    if f.name == "avatars":
                        continue
                    try:
                        f.unlink()
                    except Exception:
                        pass
        if AVATAR_DIR.exists():
            avatars_in_use = set()
            for row in conn.execute("SELECT avatar_path FROM users WHERE avatar_path != ''").fetchall():
                if row["avatar_path"]:
                    avatars_in_use.add(Path(row["avatar_path"]).name)
            for f in AVATAR_DIR.iterdir():
                if f.is_file() and f.name not in avatars_in_use:
                    try:
                        f.unlink()
                    except Exception:
                        pass
    elif task_name == "db_vacuum":
        conn.execute("VACUUM")
    conn.close()
    _save_task_run(task_name)
    _append_audit("run_task", {"task": task_name})
    return RedirectResponse("/system?success=Task completed", status_code=302)
