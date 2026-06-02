#!/usr/bin/env python3
"""
hexfeed - site server (static files for promotional site)
Serves hexfeed-site on a separate port for a dedicated .onion.
"""

import argparse
import uvicorn
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

HOST = "127.0.0.1"
PORT = 8081

_candidates = [
    Path(__file__).resolve().parent / "site",
    Path(__file__).resolve().parent.parent / "site",
]
SITE_DIR = next((d for d in _candidates if d.exists()), None)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

if SITE_DIR:
    app.mount("/", StaticFiles(directory=str(SITE_DIR), html=True), name="site")
else:
    @app.get("/")
    def no_site():
        return {"error": "site directory not found"}


def main():
    parser = argparse.ArgumentParser(description="hexfeed site server")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    uvicorn.run(
        app,
        host=HOST,
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
