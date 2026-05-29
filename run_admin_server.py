#!/usr/bin/env python3
"""
hexfeed - Admin Server
=======================
Web panel for hexfeed administration.
RESTRICTED ACCESS: runs only on 127.0.0.1:8001.

Usage:
  python run_admin_server.py [--setup] [--port PORT]

  --setup    : Configure/reconfigure the admin password
  --port     : Alternative port (default: 8001)

Requirements:
  - The main hexfeed server must be running (database must exist)
  - The password is stored as bcrypt hash in .admin_password
"""

import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from server.admin_server import (
    app, ADMIN_HOST, ADMIN_PORT, _ensure_admin_password,
    _load_admin_hash, _append_audit,
)
import bcrypt


def setup_password():
    """Configure or reconfigure the admin password."""
    from pathlib import Path
    pw_file = Path(BASE_DIR) / ".admin_password"
    print("=" * 60)
    print("  \u2b21 hexfeed Admin — Password Setup")
    print("=" * 60)
    print("  Set the admin password (min. 8 characters):")
    while True:
        try:
            pwd = input("  Password: ").strip()
            if len(pwd) < 8:
                print("  Password must be at least 8 characters.")
                continue
            confirm = input("  Confirm: ").strip()
            if pwd != confirm:
                print("  Passwords do not match.")
                continue
            pwd_hash = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()
            pw_file.write_text(pwd_hash)
            print("  Password configured successfully!")
            return
        except (EOFError, KeyboardInterrupt):
            print("\n  Operation canceled.")
            return


def main():
    import uvicorn

    port = ADMIN_PORT
    if "--setup" in sys.argv:
        setup_password()
        return
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    if not _ensure_admin_password():
        print("Admin not configured. Run with --setup first.")
        sys.exit(1)

    print(f"  hexfeed Admin running on http://{ADMIN_HOST}:{port}")
    print("  Restricted to localhost (127.0.0.1)")
    print()

    uvicorn.run(
        app,
        host=ADMIN_HOST,
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
