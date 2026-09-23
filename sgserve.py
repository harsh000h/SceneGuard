"""SceneGuard server. Keep this window open while you watch.

    py -3.12 sgserve.py --root sceneguard/manis

The browser extension asks this server "what does my family block in this title?"
and gets back time spans. Only nudity and sex can ever be skipped or blurred -
violence, gore and language are marked, never cut, and that rule is enforced here
as well as in the extension, so a hand-edited list cannot change it.
"""
from __future__ import annotations
import argparse, json, re, unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SKIP_ELIGIBLE = ("nudity", "sex")
HERE = Path(__file__).resolve().parent
JUNK = re.compile(r"\b(english|hindi|tamil|telugu|dual|audio|movie|film|hd|fhd|4k|uncut|extended)\b")
TAIL = re.compile(r"^(.*?)[ ](?:(?:19|20)\d{2}|\d{3,4}p|4k|8k|x26[45]|h26[45]|hevc|av1"
                  r"|bluray|bdrip|web-?rip|web-?dl|hdtv|dvdrip|remux|uncut|dd5[.1]|dts[.-]?hd"
                  r"|5[.1]ch|multi|eng|hin|tam|tel)[ ]*$")


def norm(s: str) -> str:
    """`Baahubali 2 (2017) [1080p] [English]` -> `baahubali 2`. Deliberately strict:
    two different titles must never fuzzy-match, because a wrong match skips the wrong
    90 seconds. Unknown titles answer 404 and the extension plays unfiltered."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(.*?\)", " ", s)
    s = JUNK.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def core(s: str) -> str:
    """norm() with trailing year/quality junk dropped, repeatedly. `mark` names a list
    after the FILE (`Sita.Ramam.2024.1080p.mkv`) but Netflix's tab bar says only
    `Sita Ramam`, so without this the two never meet. Used as a fallback only, and only
    when exactly one list shares that core, so `Blade Runner` != `Blade Runner 2049`."""
    n = norm(s)
    while True:
        m = TAIL.match(n)
        if not m or len(m.group(1)) < 3:
            return n
        n = m.group(1)


def scan(root: Path) -> list:
    out = []
    for f in sorted(root.rglob("*.sceneguard.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  skipped {f.name}: {type(exc).__name__}", flush=True)
            continue
        title = raw.get("title") or re.sub(r"\.sceneguard$", "", f.stem, flags=re.I)
        out.append({"title": title, "video_id": raw.get("video_id", ""),
                    "segments": raw.get("segments", []), "key": norm(title), "core": core(title)})
    return out


def pick(manus: list, title: str, film_id: str):
    if film_id:
        for m in manus:
            if norm(m["video_id"]) == norm(film_id):
                return m
    for m in manus:
        if m["key"] == norm(title):
            return m
    hits = [m for m in manus if m["core"] == core(title)]
    return hits[0] if len(hits) == 1 else None


def with_modes(m: dict) -> dict:
    segs = []
    for s in m["segments"]:
        adult = [c for c in (s.get("categories") or []) if c in SKIP_ELIGIBLE]
        want = s.get("mode") or ("skip" if adult else "mark_only")
        ok = bool(adult) and want == "skip" and float(s.get("confidence", 0.5)) >= 0.4
        segs.append({**s, "mode": "skip" if ok else ("mark_only" if want == "skip" else want),
                     "blocked": bool(adult)})
    return {"title": m["title"], "video_id": m["video_id"], "segments": segs,
            "skip_eligible": list(SKIP_ELIGIBLE)}


class H(BaseHTTPRequestHandler):
    root = Path(".")

    def _send(self, code, body, kind="application/json"):
        if not isinstance(body, bytes):
            body = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", f"{kind}; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        # Netflix is https, this server is http://localhost. The browser blocks that
        # read unless CORS allows it, so without this line the extension looks dead
        # while the server log shows every request arriving fine.
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "content-type")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/v1/manifest":
            m = pick(scan(self.root), q.get("title", ""), q.get("film_id", ""))
            if not m:
                self._send(404, {"error": "no list for that title",
                                "titles_you_have": [x["title"] for x in scan(self.root)]})
            else:
                self._send(200, with_modes(m))
            return
        if u.path == "/":
            rows = "".join(f"<tr><td>{x['title']}</td><td>{len(x['segments'])}</td></tr>"
                           for x in scan(self.root))
            self._send(200, "<meta charset=utf-8><title>SceneGuard</title>"
                       f"<p>Reading <code>{self.root}</code></p>"
                       "<table cellpadding=6><tr><th>Title</th><th>Spans</th></tr>"
                       + (rows or "<tr><td colspan=2>nothing yet - make a list with the mark command</td></tr>")
                       + "</table>", kind="text/html")
            return
        self._send(404, {"error": "use /v1/manifest?title=... or /"})

    def do_POST(self):
        """The Mark keypress lands here. Stored, never applied: two homes must report
        the same span before it turns into a skip, so one person cannot rewrite a film."""
        try:
            n = int(self.headers.get("content-length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "unreadable JSON"})
        key = norm(b.get("title") or b.get("filmId") or "untitled")
        who = f"{b.get('host')}|{b.get('filmId')}"
        inbox = self.root / "inbox.json"
        all_votes = json.loads(inbox.read_text(encoding="utf-8")) if inbox.exists() else {}
        box = all_votes.setdefault(key, {})
        refused = 0
        for s in b.get("segments") or []:
            cats = [c for c in (s.get("categories") or []) if c]
            if not [c for c in cats if c in SKIP_ELIGIBLE]:
                refused += 1
            seen = box.setdefault(f"{s.get('start')}-{s.get('end')}", {"who": [], "seg": s})
            if who not in seen["who"]:
                seen["who"].append(who)
        inbox.write_text(json.dumps(all_votes, indent=2, sort_keys=True), encoding="utf-8")
        self._send(200, {"needed": 1, "stored_for_review": True, "refused_non_eligible": refused})

    def log_message(self, fmt, *a):
        print("  ", fmt % a, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="SceneGuard manifest server")
    ap.add_argument("--root", default="manis",
                    help="folder holding *.sceneguard.json; next to this file, or inside sceneguard/")
    ap.add_argument("--port", type=int, default=8788)
    a = ap.parse_args()
    # Look for the folder next to this file first, then one level in, so it works
    # whether sgserve.py sits in the repo root or in the sceneguard folder itself.
    root = Path(a.root)
    if not root.is_absolute():
        for cand in (HERE / a.root, HERE / "sceneguard" / a.root, Path(a.root)):
            if cand.is_dir():
                root = cand
                break
    if not root.is_dir():
        print(f"  no folder at {root}\n"
              f"  make one, then: py -3.12 -m sceneguard mark movie.mkv --start 3180 "
              f"--end 3244 --cat sex --verified --out {a.root}", flush=True)
        return 2
    H.root = root
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    manus = scan(root)
    print(f"  SceneGuard ready: http://localhost:{a.port}", flush=True)
    print(f"  folder {root}", flush=True)
    print(f"  {len(manus)} title(s): " + (", ".join(m["title"] for m in manus[:8]) or "none yet"), flush=True)
    print("  keep this window open - press Ctrl+C to stop", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped", flush=True)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
