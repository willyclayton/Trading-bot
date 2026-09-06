"""Request-level behaviour shared by the local server and the Vercel function."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from sim.app import App
from sim.store import Busy, MemoryStore, RedisStore, _decode, _encode


def until_pending(app):
    for _ in range(400):
        st = app.state()
        if st["pending"]:
            return st
        app.act("step", {"days": 1})
    raise AssertionError("no pending proposal")


class Routing(unittest.TestCase):
    def setUp(self):
        self.app = App(MemoryStore(), seed=11)

    def test_state_and_unknown_routes(self):
        code, payload = self.app.handle("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(payload["backend"], "memory")
        self.assertEqual(self.app.handle("GET", "/nope")[0], 404)
        self.assertEqual(self.app.handle("POST", "/api/nonsense", {})[0], 404)
        self.assertEqual(self.app.handle("GET", "/api/step")[0], 405)

    def test_step_returns_state(self):
        code, payload = self.app.handle("POST", "/api/step", {"days": 3})
        self.assertEqual(code, 200)
        self.assertEqual(payload["stepped"], 3)
        self.assertEqual(payload["state"]["stats"]["steps"], 3)

    def test_approve_reject_status_codes(self):
        st = until_pending(self.app)
        p = st["pending"][0]
        self.assertEqual(self.app.handle("POST", "/api/reject", {"id": p["id"], "token": "bad", "category": "data"})[0], 409)
        self.assertEqual(self.app.handle("POST", "/api/reject", {"id": p["id"], "token": p["token"], "category": "vibes"})[0], 400)
        code, payload = self.app.handle("POST", "/api/approve", {"id": p["id"], "token": p["token"]})
        self.assertEqual(code, 200)
        self.assertEqual(payload["proposal"]["status"], "approved")
        self.assertEqual(self.app.handle("POST", "/api/approve", {"id": p["id"], "token": p["token"]})[0], 409)
        self.assertEqual(self.app.handle("POST", "/api/approve", {"id": "zzz", "token": "x"})[0], 404)

    def test_tick_is_a_no_op_unless_auto(self):
        before = self.app.state()["stats"]["steps"]
        code, payload = self.app.handle("GET", "/api/tick")
        self.assertEqual(code, 200)
        self.assertFalse(payload["ticked"])
        self.assertEqual(payload["stats"]["steps"], before)
        self.app.handle("POST", "/api/auto", {"on": True})
        code, payload = self.app.handle("POST", "/api/tick", {})
        self.assertTrue(payload["ticked"])
        self.assertEqual(payload["stats"]["steps"], before + 1)
        self.assertTrue(payload["stats"]["auto"])

    def test_auto_flag_survives_a_cold_start(self):
        store = MemoryStore()
        App(store, seed=3).handle("POST", "/api/auto", {"on": True})
        fresh = App(store, seed=3)          # new process, same store
        self.assertTrue(fresh.state()["stats"]["auto"])
        self.assertTrue(fresh.tick()["ticked"])

    def test_reset_keeps_auto_and_clears_history(self):
        self.app.handle("POST", "/api/auto", {"on": True})
        self.app.handle("POST", "/api/step", {"days": 30})
        code, payload = self.app.handle("POST", "/api/reset", {})
        self.assertEqual(code, 200)
        self.assertEqual(payload["state"]["stats"]["steps"], 0)
        self.assertTrue(payload["state"]["stats"]["auto"])


class FakeUpstash:
    """Just enough of the Upstash REST protocol: GET, SET [NX PX], DEL, EVAL(release)."""

    def __init__(self, token="t0k"):
        self.data = {}
        self.token = token
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    return self._reply(401, {"error": "Unauthorized"})
                cmd = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append(cmd[0])
                name, args = cmd[0].upper(), cmd[1:]
                if name == "GET":
                    result = outer.data.get(args[0])
                elif name == "SET":
                    key, val, *opts = args
                    if "NX" in opts and key in outer.data:
                        result = None
                    else:
                        outer.data[key] = val
                        result = "OK"
                elif name == "DEL":
                    result = int(outer.data.pop(args[0], None) is not None)
                elif name == "EVAL":
                    _script, _n, key, tok = args
                    result = int(outer.data.get(key) == tok and outer.data.pop(key, None) is not None)
                else:
                    return self._reply(400, {"error": f"unknown {name}"})
                self._reply(200, {"result": result})

            def _reply(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Redis(unittest.TestCase):
    def setUp(self):
        self.fake = FakeUpstash()
        self.store = RedisStore(self.fake.url, "t0k")

    def tearDown(self):
        self.fake.close()

    def test_snapshot_round_trip_is_compressed(self):
        snap = {"a": [1, 2, 3] * 500, "b": "x" * 5000}
        self.store.save(snap)
        self.assertLess(len(self.fake.data["sim:snapshot"]), len(json.dumps(snap)) / 4)
        self.assertEqual(self.store.load(), snap)
        self.assertEqual(_decode(_encode(snap)), snap)
        self.store.reset()
        self.assertIsNone(self.store.load())

    def test_lock_excludes_second_writer_and_is_released(self):
        with self.store.lock():
            with self.assertRaises(Busy), self.store.lock():
                pass
            self.assertIn("sim:snapshot:lock", self.fake.data)
        self.assertNotIn("sim:snapshot:lock", self.fake.data)
        with self.store.lock():
            pass

    def test_bad_token_raises(self):
        bad = RedisStore(self.fake.url, "wrong")
        with self.assertRaises(RuntimeError):
            bad.load()

    def test_app_end_to_end_on_redis(self):
        app = App(self.store, seed=4)
        self.assertEqual(app.backend, "redis")
        app.handle("POST", "/api/auto", {"on": True})
        for _ in range(5):
            code, payload = app.handle("POST", "/api/tick", {})
            self.assertEqual(code, 200)
            self.assertTrue(payload["ticked"])
        other = App(self.store, seed=4)              # a second serverless instance
        self.assertEqual(other.state()["stats"]["steps"], 5)
        with self.store.lock():
            self.assertEqual(other.handle("POST", "/api/tick", {})[0], 423)


if __name__ == "__main__":
    unittest.main()
