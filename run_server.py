#!/usr/bin/env python3
"""
hexfeed - server launcher
Usage: python run_server.py [--host HOST] [--port PORT]

Starts the hexfeed FastAPI server.
Default: http://127.0.0.1:8000
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="hexfeed server")
    parser.add_argument("--host", default="127.0.0.1", help="Endereço para escutar")
    parser.add_argument("--port", type=int, default=8000, help="Porta")
    parser.add_argument("--reload", action="store_true", help="Reload automático em alterações")
    args = parser.parse_args()

    print(f"  ⬡ hexfeed server starting on {args.host}:{args.port}")
    print()

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
