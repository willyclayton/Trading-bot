"""Local development server.

    python -m sim.server [--port 8000] [--db sim.db] [--seed 7]

Standard library only. Serves the console from ``public/`` and the API from
:class:`sim.app.App`. A background thread calls ``tick()`` on the Auto
interval so Auto runs even with no browser open — the one thing the Vercel
deployment cannot do (there, open tabs and the daily cron drive the ticks).
"""
from __future__ import annotations

import argparse
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from .app import App
from .store import Busy, from_env
from .web import make_handler

PUBLIC = Path(__file__).resolve().parent.parent / "public"


def auto_loop(app: App, stop: threading.Event) -> None:
    while not stop.is_set():
        interval = 1.5
        try:
            out = app.tick()
            interval = out["config"]["auto_interval_s"]
        except Busy:
            pass
        stop.wait(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trading-bot simulator console")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=os.environ.get("SIM_DB", "sim.db"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-auto-thread", action="store_true",
                    help="behave like the serverless deployment: only requests tick")
    args = ap.parse_args(argv)

    os.environ.setdefault("SIM_DB", args.db)
    store = from_env(args.db)
    app = App(store, seed=args.seed)
    stop = threading.Event()
    if not args.no_auto_thread:
        threading.Thread(target=auto_loop, args=(app, stop), name="auto", daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(app, PUBLIC))
    auto = app.state()["stats"]["auto"]
    print(f"simulator console on http://{args.host}:{args.port}  "
          f"(store: {app.backend}, auto={'ON' if auto else 'off'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        store.close()


if __name__ == "__main__":
    main()
