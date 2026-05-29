"""
hexfeed-server - server + admin launcher (localhost only + Tor)

⚠️  Binds to 127.0.0.1 ONLY — no external IP exposure.
   External access is only possible via Tor onion service.
   Tor starts automatically if the tor binary is available.
"""
import sys
import os
import time
import subprocess
import signal

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="hexfeed server (localhost only)")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    parser.add_argument("--admin-port", type=int, default=8001, help="Admin panel port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    args = parser.parse_args()

    main_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app",
         "--host", HOST, "--port", str(args.port),
         "--log-level", "warning", "--no-access-log",
         "--no-server-header"] + (["--reload"] if args.reload else []),
        cwd=BASE_DIR,
    )

    admin_pw = os.path.join(BASE_DIR, ".admin_password")
    admin_proc = None
    if os.path.exists(admin_pw) and os.path.getsize(admin_pw) > 0:
        time.sleep(0.5)
        admin_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.admin_server:app",
             "--host", "127.0.0.1", "--port", str(args.admin_port),
             "--log-level", "warning", "--no-access-log",
             "--no-server-header"],
            cwd=BASE_DIR,
        )

    print(f"  ⬡ hexfeed:         http://{HOST}:{args.port}")
    if admin_proc:
        print(f"  ⬡ Admin panel:     http://{HOST}:{args.admin_port}")
    print("  🧅 Tor onion:       auto if tor binary is available")
    print()
    print("  Press Ctrl+C to stop all services.")
    print()

    def cleanup(sig, frame):
        for p in [main_proc, admin_proc]:
            if p:
                p.terminate()
        for p in [main_proc, admin_proc]:
            if p:
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        main_proc.wait()
        if admin_proc:
            admin_proc.wait()
    except KeyboardInterrupt:
        cleanup(None, None)


if __name__ == "__main__":
    main()
