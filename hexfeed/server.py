"""Entry point for hexfeed server + admin panel."""
import sys
import os
import time
import subprocess
import signal

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="hexfeed server")
    parser.add_argument("--host", default="127.0.0.1", help="Server address")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    parser.add_argument("--admin-port", type=int, default=8001, help="Admin panel port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    args = parser.parse_args()

    main_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app",
         "--host", args.host, "--port", str(args.port),
         "--log-level", "info"] + (["--reload"] if args.reload else []),
        cwd=BASE_DIR,
    )

    admin_pw = os.path.join(BASE_DIR, ".admin_password")
    admin_proc = None
    if os.path.exists(admin_pw) and os.path.getsize(admin_pw) > 0:
        time.sleep(0.5)
        admin_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.admin_server:app",
             "--host", "127.0.0.1", "--port", str(args.admin_port),
             "--log-level", "info"],
            cwd=BASE_DIR,
        )

    print(f"  ⬡ hexfeed server: http://{args.host}:{args.port}")
    if admin_proc:
        print(f"  ⬡ Admin panel:    http://127.0.0.1:{args.admin_port}")
    print()
    print("  Press Ctrl+C to stop all services.")

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
