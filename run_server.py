#!/usr/bin/env python3
"""
hexfeed - server launcher (localhost only + Tor)
Usage: python run_server.py [--port PORT]

⚠️  Binds to 127.0.0.1 ONLY — no external IP exposure.
   External access is only possible via Tor onion service.
   Tor starts automatically if the tor binary is available.
"""

import argparse
import uvicorn

HOST = "127.0.0.1"
PORT = 8000


def main():
    parser = argparse.ArgumentParser(description="hexfeed server (localhost only)")
    parser.add_argument("--port", type=int, default=PORT, help="Porta")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    args = parser.parse_args()

    print(f"  ⬡ hexfeed: http://{HOST}:{args.port}")
    print()

    uvicorn.run(
        "server.main:app",
        host=HOST,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
