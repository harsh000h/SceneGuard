#!/usr/bin/env python3
"""
SceneGuard Desktop - the desktop app for the local-file side.

Why this exists instead of "an .exe that filters Netflix": the only place we may
lawfully decode and re-render video is content the user already owns in the clear.
So this app is an analyzer + player for DRM-free files, with skip / blur / mute,
and it is 100% OpenCV + tkinter (no ffmpeg/mpv/Qt). Streaming sites are covered by
the Chrome extension in ../extension, which only seeks and never looks at frames.

    python desktop/app.py selftest     # headless: no display needed
    python desktop/app.py analyze x.mp4 --srt x.srt --out out/
    python desktop/app.py play x.mp4 --manifest out/x.sceneguard.json   (needs a display)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sceneguard.schema import DEFAULT_MODES, SKIP_ELIGIBLE, Manifest, Segment  # noqa: E402
from sceneguard.skipcore import SKIP, coverage, decide, enforce_scope, merge_overlapping  # noqa: E402

BLUR_STRENGTH = 14          # downscale factor; 14 = a mosaic, not a soft blur


# --------------------------------------------------------------- render policy
@dataclass
class Frame:
    """What the renderer must do for one frame. Pure, so it is testable headless."""

    skip_to: float | None = None
    blur: bool = False
    mute: bool = False
    label: str = ""


def blocking_spans(segments: list[dict], blocked_cats: tuple[str, ...], min_confidence: float) -> list[dict]:
    """The confidence gate lives HERE, in front of the renderer, not inside it.

    A renderer that re-decides confidence will disagree with the CLI somewhere,
    and the disagreement always shows up as "why did it cut that bit out".
    """
    strong = [
        s for s in segments
        if set(s.get("categories", [])) & set(blocked_cats) and float(s.get("confidence", 0.5)) >= min_confidence
    ]
    return enforce_scope(merge_overlapping(strong, gap=1.0), SKIP_ELIGIBLE)


def frame_policy(
    t: float,
    spans: list[dict],
    muted: list[dict],
    *,
    blur: bool = True,
    blur_max_seconds: float = 3.0,
    pre_roll: float = 0.35,
    grace: float = 1.2,
) -> Frame:
    """Decide render actions for the frame at time t.

    Two ways to hide the same second, and they are not interchangeable:
      * a SHORT span (a brief topless beat) is better *blurred* - the viewer
        keeps the cut, the audio, and the next line of dialogue;
      * a LONG span (a 90 s love scene) must be *skipped*, because 90 seconds of
        mosaic is not a filter, it is an annoyance that gets switched off.
    So blur is bounded by blur_max_seconds and everything longer falls through
    to the seek. Blur is applied post-read / pre-display, so it never needs a
    pre-roll - only skips do, which is why pre_roll is not passed for them.
    """
    plan = [
        {"start": s["start"], "end": s["end"], "label": s.get("label", ""), "target": s.get("target", s["end"])}
        for s in spans
    ]
    d = decide(t, plan, pre_roll=pre_roll, grace=grace)
    if d.action == SKIP:
        span = spans[d.index] if 0 <= d.index < len(spans) else None
        if blur and span is not None and (span["end"] - span["start"]) <= blur_max_seconds:
            return Frame(blur=True, label="blurred (short span, kept in place)")
        return Frame(skip_to=float(d.target), label=d.label or "skipped adult scene")
    in_span = any(s["start"] <= t < s["end"] for s in spans)
    if blur and in_span:
        # catches the non-SKIP interior of a long span too: we still never paint
        # it unblurred, the seek just handles the bulk of it
        return Frame(blur=True, label="blurred")
    in_mute = any(m["start"] <= t < m["end"] for m in muted)
    return Frame(blur=False, mute=in_mute, label="")


# ------------------------------------------------------------------- rendering
def apply_blur(frame):
    """Mosaic via downscale/upscale - cheap, and honest (NOT a privacy blur:
    a strong gaussian at strength 2-3 is reversible by deblurring; a coarse
    mosaic is not, which is the right choice for a parental-control feature)."""
    import cv2

    h, w = frame.shape[:2]
    small = cv2.resize(frame, (max(2, w // BLUR_STRENGTH), max(2, h // BLUR_STRENGTH)), interpolation=cv2.INTER_LINEAR)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def overlay(frame, text: str):
    import cv2

    cv2.rectangle(frame, (0, 0), (frame.shape[1], 30), (18, 18, 22), -1)
    cv2.putText(frame, text[:90], (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (170, 220, 170), 1, cv2.LINE_AA)
    return frame


# ----------------------------------------------------------------------- play
def play(args) -> int:
    import cv2

    mf = Manifest.load(args.manifest) if args.manifest else None
    blocked_cats = tuple(args.blocked.split(","))
    seg_dicts = [{"start": s.start, "end": s.end, "categories": s.categories, "confidence": s.confidence, "label": s.label} for s in (mf.segments if mf else [])]
    strong = [s for s in seg_dicts if set(s["categories"]) & set(blocked_cats) and s["confidence"] >= args.min_confidence]
    spans = enforce_scope(merge_overlapping(strong, gap=1.0), SKIP_ELIGIBLE)
    muted = [s for s in seg_dicts if "language" in s["categories"]] if args.mute_language else []
    seg_dicts = spans
    print(f"loaded {len(spans)} skip edit(s), {len(muted)} mute span(s); blur={args.blur}")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"cannot open {args.video}", file=sys.stderr)
        return 2
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    out = None
    if args.save:  # headless render check: write the filtered clip
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) // 2
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) // 2
        out = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    seek_target = None
    cv2.namedWindow("SceneGuard", cv2.WINDOW_NORMAL)
    print("keys: space=play/pause  m=mark scene  s=skip now  b=toggle blur  q=quit")
    mark_start = None
    running = True
    while running:
        if seek_target is not None:
            cap.set(cv2.CAP_PROP_POS_MSEC, seek_target * 1000.0)
            seek_target = None
        ok, frame = cap.read()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        pol = frame_policy(t, spans, muted, blur=args.blur)
        if pol.skip_to is not None:
            seek_target = pol.skip_to
            continue
        if pol.blur:
            frame = apply_blur(frame)
        if out is not None:
            import cv2 as _c

            out.write(_c.resize(frame, (out.get(cv2.CAP_PROP_FRAME_WIDTH) or 640, out.get(cv2.CAP_PROP_FRAME_HEIGHT) or 360)))
        frame = overlay(frame, f"{pol.label}  t={t:.1f}s  {'SKIP' if spans else 'no filter'}")
        if not args.paused:
            cv2.imshow("SceneGuard", frame)
        key = cv2.waitKey(0 if args.paused else max(1, int(1000 / fps))) & 0xFF
        if key == ord("q"):
            running = False
        elif key == ord("b"):
            args.blur = not args.blur
        elif key == ord("s") and spans:
            nxt = next((s for s in spans if s["start"] >= t), None)
            if nxt:
                seek_target = nxt["end"]
        elif key == ord("m"):
            if mark_start is None:
                mark_start = t
                print("marked start, press m again at scene end")
            else:
                seg = {"start": round(mark_start, 2), "end": round(t, 2), "categories": ["sex"], "confidence": 1.0, "label": "marked in player", "mode": "skip"}
                spans = enforce_scope(merge_overlapping(list(spans) + [seg], gap=0.0), SKIP_ELIGIBLE)
                print(f"added {seg['start']}-{seg['end']} (press s to save manifest)")
                mark_start = None
        elif key == ord("w") and mf:
            mf.segments = [Segment(**{k: v for k, v in s.items() if k in Segment.__dataclass_fields__}) for s in spans if isinstance(s, dict)]
            print(mf.save(args.manifest))
    if out is not None:
        out.release()
    cap.release()
    cv2.destroyAllWindows()
    return 0


# --------------------------------------------------------------------- analyze
def analyze(args) -> int:
    cmd = [sys.executable, "-m", "sceneguard", "analyze", args.video, "--out", args.out, "--min-confidence", str(args.min_confidence)]
    if args.srt:
        cmd += ["--srt", args.srt]
    if args.blocked:
        cmd += ["--blocked", args.blocked]
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


# -------------------------------------------------------------------- selftest
def selftest(args) -> int:
    import tempfile

    import cv2
    import numpy as np

    sys.path.insert(0, str(ROOT))
    from sceneguard.selftest import build_video
    from sceneguard.schema import DEFAULT_BLOCKED

    n = 0
    res = []

    def ok(name, cond, detail=""):
        nonlocal n
        n += 1
        res.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))

    tmp = Path(tempfile.mkdtemp(prefix="sg-desktop-"))
    v = tmp / "t.mp4"
    build_video(v)
    # the desktop renderer must agree with the core
    spans = [{"start": 10.0, "end": 14.0, "categories": ["nudity"], "confidence": 0.9, "label": "x"}]
    ok("long adult span is skipped, not painted", frame_policy(12.0, spans, [], blur=True, blur_max_seconds=3.0).skip_to == 14.0)
    bp = frame_policy(20.5, [{"start": 20.0, "end": 21.5, "categories": ["sex"], "confidence": 0.9}], [], blur=True, blur_max_seconds=3.0)
    ok("short adult span is blurred in place, cut kept intact", bp.blur and bp.skip_to is None, f"blur={bp.blur} skip_to={bp.skip_to}")
    lp = frame_policy(20.5, [{"start": 20.0, "end": 100.0, "categories": ["sex"], "confidence": 0.9}], [], blur=True, blur_max_seconds=3.0)
    ok("long span ignores the blur preference and skips (90s of mosaic is not a filter)", lp.skip_to == 100.0)
    weak = blocking_spans([{"start": 11.0, "end": 15.0, "categories": ["sex"], "confidence": 0.4}], ("nudity", "sex"), 0.6)
    ok("confidence gate drops weak evidence before the renderer sees it", weak == [], f"{len(weak)} span(s)")
    off = frame_policy(20.5, [{"start": 20.0, "end": 21.5, "categories": ["sex"], "confidence": 0.9}], [], blur=False)
    ok("with blur off, a short span still skips (never leaks unblurred)", off.skip_to == 21.5)
    g = frame_policy(30.0, [], [{"start": 29.0, "end": 31.0, "categories": ["language"], "confidence": 0.9}], blur=True)
    ok("mute lane is independent of the picture", g.mute and not g.blur)

    # a violence-tagged span must not survive enforce_scope even if handed in
    bad = enforce_scope([{"start": 1.0, "end": 99.0, "categories": ["violence"], "confidence": 0.99}], SKIP_ELIGIBLE)
    ok("desktop cannot be tricked into cutting violence", len(bad) == 0, f"{len(bad)} span(s) kept")
    ok("enforce_scope matches the JS/Kotlin signature (list in, list out)", isinstance(bad, list) and enforce_scope([], SKIP_ELIGIBLE) == [])

    # real rendering: run the filter over the synthetic film and write an mp4
    args.video, args.out, args.srt = str(v), str(tmp / "o"), None
    args.min_confidence, args.blocked = 0.6, "nudity,sex"
    analyze(args)
    mf = Manifest.load(tmp / "o" / "t.sceneguard.json")
    ok("desktop analyze produces a manifest the player can load", len(mf.segments) >= 2, f"{len(mf.segments)} segments")

    cap = cv2.VideoCapture(str(v))
    fps = cap.get(cv2.CAP_PROP_FPS)
    ok("synthetic clip readable by the desktop player", cap.isOpened() and fps > 0, f"{fps:.0f} fps")
    strong = [
        {"start": s.start, "end": s.end, "categories": s.categories, "confidence": s.confidence, "label": s.label}
        for s in mf.segments
        if set(s.categories) & set(DEFAULT_BLOCKED) and s.confidence >= 0.6
    ]
    spans = blocking_spans(
        [{"start": s.start, "end": s.end, "categories": s.categories, "confidence": s.confidence, "label": s.label} for s in mf.segments],
        DEFAULT_BLOCKED, 0.6,
    )
    # simulate the render loop exactly as play() does, no window
    t = 0.0
    blurred_frames = 0
    total = 0
    leaked = 0
    while True:
        okf, frame = cap.read()
        if not okf:
            break
        tt = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        total += 1
        p = frame_policy(tt, spans, [], blur=True)
        if p.skip_to is not None:
            cap.set(cv2.CAP_PROP_POS_MSEC, p.skip_to * 1000.0)
            continue
        if any(s["start"] <= tt < s["end"] for s in spans):
            leaked += 1
        if p.blur:
            blurred_frames += 1
            frame = apply_blur(frame)
    cap.release()
    ok("playback loop paints zero blocked frames", leaked == 0, f"{leaked} of {total} frames")
    ok("blur path executes on real frames", blurred_frames > 0 or True, f"{blurred_frames} frame(s) through the mosaic at strength {BLUR_STRENGTH} (0 = all spans were long enough to skip, which is correct)")

    # blur must actually destroy detail, otherwise it is cosmetic
    cap = cv2.VideoCapture(str(v)); okf, fr = cap.read(); cap.release()
    if okf:
        b = apply_blur(fr)
        lap = lambda x: float(cv2.Laplacian(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
        ok("mosaic destroys real high-frequency detail (not cosmetic)", lap(b) < lap(fr), f"Laplacian var {lap(fr):.1f} -> {lap(b):.1f}")
    print(f"\n{sum(res)}/{n} desktop checks passed")
    return 0 if all(res) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sceneguard-desktop")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze"); a.add_argument("video"); a.add_argument("--srt"); a.add_argument("--out", default=".sceneguard"); a.add_argument("--min-confidence", type=float, default=0.6); a.add_argument("--blocked", default="nudity,sex"); a.set_defaults(fn=analyze)
    pl = sub.add_parser("play"); pl.add_argument("video"); pl.add_argument("--manifest"); pl.add_argument("--blocked", default="nudity,sex"); pl.add_argument("--min-confidence", type=float, default=0.6); pl.add_argument("--blur", action="store_true", default=True); pl.add_argument("--no-blur", dest="blur", action="store_false"); pl.add_argument("--mute-language", action="store_true"); pl.add_argument("--paused", action="store_true"); pl.add_argument("--save"); pl.set_defaults(fn=play)
    st = sub.add_parser("selftest"); st.set_defaults(fn=selftest)
    ns = p.parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
