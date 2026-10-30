"""GhostCut Director engine: serves the app and its JSON API on 127.0.0.1.

    python app/engine.py [port]     →  http://127.0.0.1:7791/

GET  /api/project            the cut with accepted edits applied, plus the edit list
POST /api/muse               {text, at} → Spark's proposal, every operation with its checks
POST /api/accept             {id, pick?} → apply a proposal's unblocked operations
POST /api/undo               {edit?} → remove that edit (default: the last one)
GET  /api/frame?tag=&t=      a still from a library shot (only tags in the project's library)

Run with the Python that has the Meta client installed (guestlist.meta) for Muse; everything else works without it.
A fresh clone has no project (app/ui/demo/ is gitignored): build one with app/make_demo.py.

Only the app's own files are served (app/ui/, docs/brand/), only to a 127.0.0.1/localhost Host header, and the
API takes JSON bodies only, so other pages in a browser can't read files or spend Meta credits through it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import muse  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "app" / "ui" / "demo"
STATIC = ("/app/ui/", "/docs/brand/")
NO_PROJECT = "No project yet: build one with app/make_demo.py from a finished Director job (see docs/APP-DESIGN.md)."


class Engine:
    def __init__(self, demo: Path = DEMO):
        self.demo = demo
        p = demo / "project.json"
        self.base = json.loads(p.read_text()) if p.exists() else None
        self.edits_file = demo / "edits.json"
        self.edits = json.loads(self.edits_file.read_text()) if self.edits_file.exists() else []
        self.spend_file = demo / "muse_spend.json"
        self.spend = json.loads(self.spend_file.read_text()) if self.spend_file.exists() else {"usd": 0.0, "calls": 0}
        self.proposals: dict[str, dict] = {}
        self.lock = threading.Lock()

    def project(self) -> dict:
        if self.base is None:
            raise FileNotFoundError(NO_PROJECT)
        p = muse.apply(self.base, self.edits)
        return {"project": p, "edits": [{k: e[k] for k in ("id", "request", "labels", "usd")} for e in self.edits],
                "engine": {"live": True, "museSpent": round(self.spend["usd"], 4), "museCalls": self.spend["calls"]}}

    def ask(self, text: str, at: float) -> dict:
        if self.base is None:
            raise FileNotFoundError(NO_PROJECT)
        current = muse.apply(self.base, self.edits)
        data, meta = muse.ask(current, text, at)
        if not meta["cached"]:
            with self.lock:
                self.spend = {"usd": self.spend["usd"] + meta["usd"], "calls": self.spend["calls"] + 1}
                self.spend_file.write_text(json.dumps(self.spend))
        ops = muse.validate(current, data.get("ops", []))
        pid = uuid.uuid4().hex[:8]
        self.proposals[pid] = {"request": text, "ops": ops, "usd": meta["usd"]}
        return {"id": pid, "reply": data.get("reply", ""), "answers": data.get("answers", []), "ops": ops, **meta}

    def accept(self, pid: str, pick: list[int] | None) -> dict:
        prop = self.proposals.get(pid)
        if not prop:
            raise KeyError("That proposal is gone (the engine restarted?). Ask again.")
        chosen = [op for i, op in enumerate(prop["ops"]) if not op["blocked"] and (pick is None or i in pick)]
        if not chosen:
            raise ValueError("Nothing to apply: every operation was blocked or unpicked.")
        with self.lock:
            clean = [{k: op[k] for k in muse.OP_SCHEMA["required"]} for op in chosen]
            self.edits.append({"id": f"e{int(time.time() * 1000) % 10**9}", "request": prop["request"], "ops": clean,
                               "labels": [op["label"] for op in chosen], "usd": prop["usd"]})
            self._save()
        del self.proposals[pid]
        return self.project()

    def undo(self, edit: str | None) -> dict:
        with self.lock:
            if self.edits:
                idx = next((i for i, e in enumerate(self.edits) if e["id"] == edit), len(self.edits) - 1)
                self.edits.pop(idx)
                self._save()
        return self.project()

    def _save(self) -> None:
        tmp = self.edits_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.edits, indent=1))
        os.replace(tmp, self.edits_file)

    def frame(self, tag: str, t: float) -> Path:
        e = next((x for x in (self.base or {}).get("library", []) if x["tag"] == tag), None)
        if not e or not Path(e["file"]).exists():
            raise KeyError(f"no library shot {tag!r}")
        t = max(0.0, min(t, (e.get("dur") or t + 1) - 0.1))
        out = self.demo / "frames" / f"{re.sub(r'[^A-Za-z0-9_-]', '', tag)}_{t:.1f}.jpg"
        if not out.exists():
            out.parent.mkdir(exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", e["file"], "-frames:v", "1",
                            "-vf", "scale=384:-2", "-q:v", "4", str(out)], check=True, timeout=60)
        return out


class Handler(SimpleHTTPRequestHandler):
    engine: Engine

    def log_message(self, fmt, *args):  # quiet static files, keep API calls
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def end_headers(self):
        # A local app server: never let the web view keep a stale app.css / app.js.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, code: int = 200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _local_host(self) -> bool:
        """Reject requests whose Host isn't this server (DNS rebinding)."""
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self._local_host():
            return self._json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        if url.path in ("/", "/app", "/app/"):
            self.send_response(302)
            self.send_header("Location", "/app/ui/")
            self.end_headers()
            return
        if url.path == "/api/project":
            try:
                return self._json(self.engine.project())
            except FileNotFoundError as e:
                return self._json({"error": str(e)}, 404)
        if url.path == "/api/frame":
            q = parse_qs(url.query)
            try:
                path = self.engine.frame(q.get("tag", [""])[0], float(q.get("t", ["0"])[0]))
            except (KeyError, ValueError, subprocess.SubprocessError) as e:
                return self._json({"error": str(e)}, 404)
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if not url.path.startswith(STATIC) or any(seg.startswith(".") for seg in url.path.split("/")):
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    def do_HEAD(self):
        url = urlparse(self.path)
        if not self._local_host() or not url.path.startswith(STATIC) or \
                any(seg.startswith(".") for seg in url.path.split("/")):
            self.send_error(404)
            return
        return super().do_HEAD()

    def do_POST(self):
        if not self._local_host():
            return self._json({"error": "forbidden"}, 403)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json({"error": "JSON only"}, 415)
        url = urlparse(self.path)
        try:
            body = self._body()
            if url.path == "/api/muse":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._json({"error": "Empty request"}, 400)
                return self._json(self.engine.ask(text, float(body.get("at") or 0)))
            if url.path == "/api/accept":
                return self._json(self.engine.accept(body.get("id", ""), body.get("pick")))
            if url.path == "/api/undo":
                return self._json(self.engine.undo(body.get("edit")))
            return self._json({"error": "unknown endpoint"}, 404)
        except ModuleNotFoundError:
            return self._json({"error": "Muse needs the Meta client: run the engine with the venv that has "
                                        "guestlist.meta installed."}, 503)
        except FileNotFoundError as e:
            return self._json({"error": str(e)}, 404)
        except (KeyError, ValueError) as e:
            return self._json({"error": str(e).strip("'\"")}, 400)
        except Exception as e:  # surface Spark / budget errors to the UI instead of a dropped connection
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # HTTP Range, so the video player can seek (Safari/WKWebView will not play without it).
    def send_head(self):
        path = self.translate_path(self.path)
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start = int(m[1]) if m[1] else max(0, size - int(m[2] or 0))
        end = min(int(m[2]), size - 1) if m[1] and m[2] else size - 1
        if start >= size:
            self.send_error(416)
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        left = getattr(self, "_remaining", None)
        if left is None:
            return super().copyfile(source, outputfile)
        while left > 0:
            chunk = source.read(min(left, 1 << 16))
            if not chunk:
                break
            outputfile.write(chunk)
            left -= len(chunk)


def serve(port: int = 7791, demo: Path = DEMO) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"engine": Engine(demo)})
    return ThreadingHTTPServer(("127.0.0.1", port), partial(handler, directory=str(ROOT)))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7791
    httpd = serve(port)
    print(f"GhostCut engine on http://127.0.0.1:{port}/app/ui/", flush=True)
    httpd.serve_forever()
