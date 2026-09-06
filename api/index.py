"""Vercel Python function: every ``/api/*`` request lands here (see
``vercel.json`` rewrites). The console itself is static, served from
``public/`` by Vercel.

State lives in Upstash Redis (``KV_REST_API_URL`` / ``KV_REST_API_TOKEN``,
injected by the Vercel "Upstash for Redis" integration). Without those
variables the function falls back to SQLite in ``/tmp``, which does not
survive between invocations — fine for smoke tests, useless for history.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sim.app import App  # noqa: E402
from sim.store import from_env  # noqa: E402
from sim.web import make_handler  # noqa: E402

_app = App(from_env(default_db="/tmp/sim.db"), seed=int(os.environ.get("SIM_SEED", "7")))

handler = make_handler(_app)
