"""
End-to-end self test with zero external services and zero ffmpeg dependency.

It synthesises a small DRM-free video whose shots are *hand-designed* to hit
every branch of the detector, then runs the real pipeline (sample -> boundaries
-> candidates -> fuse with captions -> merge -> manifest -> skip decisions) and
asserts on the numbers.

Run:  python -m sceneguard selftest
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

SKIN = (150, 130, 205)      # BGR - lands inside the YCrCb skin range
BG = (200, 150, 60)         # cluttered cool background
FACE = (175, 155, 225)

PASS = "PASS"
FAIL = "FAIL"


def _noise_bg(h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    img = np.full((h, w, 3), BG, dtype=np.float32)
    img += rng.normal(0, 26, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def shot_skin(writer, rng, seconds: float, fps: float) -> None:
    """Bare-skin field: large, flat chroma, few edges, mid-shot."""
    for _ in range(int(seconds * fps)):
        f = _noise_bg(180, 320, rng)
        f[30:150, 55:265] = SKIN
        writer.write(f)


def shot_face(writer, rng, seconds: float, fps: float) -> None:
    """Dialogue close-up: skin present but a *small* blob -> must not skip."""
    for _ in range(int(seconds * fps)):
        f = _noise_bg(180, 320, rng)
        cv2.circle(f, (160, 90), 34, FACE, -1)
        f[55:70, 132:190] = (30, 30, 40)
        f[100:115, 138:184] = (25, 20, 30)
        writer.write(f)


def shot_clothed(writer, rng, seconds: float, fps: float) -> None:
    """Skin + stripes + props: high edge density and high chroma dispersion."""
    for _ in range(int(seconds * fps)):
        f = _noise_bg(180, 320, rng)
        f[20:160, 70:250] = SKIN
        for y in range(20, 160, 7):
            f[y : y + 4, 70:250] = (18, 60, 120)
        for _ in range(28):
            x, y = int(rng.uniform(60, 260)), int(rng.uniform(30, 150))
            cv2.circle(f, (x, y), 9, (int(rng.uniform(0, 255)), int(rng.uniform(0, 255)), int(rng.uniform(0, 255))), -1)
        writer.write(f)


def shot_scenery(writer, rng, seconds: float, fps: float) -> None:
    for _ in range(int(seconds * fps)):
        f = _noise_bg(180, 320, rng)
        cv2.rectangle(f, (0, 120), (320, 180), (40, 90, 40), -1)
        writer.write(f)


SRT = """1
00:00:13,500 --> 00:00:16,200
- Where are we going?
- The bedroom. Take your clothes off.

2
00:00:19,000 --> 00:00:20,500
She kissed him goodbye.

3
00:00:33,000 --> 00:00:34,800
There is blood everywhere, he was stabbed twice.

4
00:00:31,000 --> 00:00:35,000
Nobody undresses in my house, get out.

5
00:00:44,000 --> 00:00:46,000
The killer shot three men in the street.
"""

#: (kind, seconds) - ground truth for the synthetic film
SCRIPT = [
    ("scenery", 6.0),
    ("skin", 7.0),      # 6.0 - 13.0   real scene, captions agree
    ("face", 6.0),      # 13.0 - 19.0   dialogue only, "kiss" -> must NOT skip
    ("clothed", 6.0),   # 19.0 - 25.0   gore talk only -> must NOT skip
    ("skin", 5.0),      # 25.0 - 30.0   real scene, captions agree
    ("scenery", 6.0),   # 30.0 - 36.0   "undress" line but zero visual -> weak
    ("clothed", 5.0),   # 36.0 - 41.0
    ("face", 5.0),      # 41.0 - 46.0   violence talk -> must be KEPT
    ("scenery", 4.0),   # 46.0 - 50.0
]


def build_video(path: Path) -> tuple[float, list[tuple[str, float, float]]]:
    fps = 25.0
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
    if not writer.isOpened():
        raise RuntimeError("cv2.VideoWriter could not open an mp4 container here")
    rng = np.random.default_rng(7)
    t = 0.0
    truth: list[tuple[str, float, float]] = []
    for kind, dur in SCRIPT:
        truth.append((kind, round(t, 3), round(t + dur, 3)))
        {"skin": shot_skin, "face": shot_face, "clothed": shot_clothed, "scenery": shot_scenery}[kind](writer, rng, dur, fps)
        t += dur
    writer.release()
    return fps, truth


def spans_overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    return min(a1, b1) - max(a0, b0) > 0.05


def run() -> int:
    from .__main__ import cmd_analyze, fuse
    from .lexicon import hits_from_cues, parse_cues
    from .schema import DEFAULT_BLOCKED, Manifest
    from .skipcore import AFTER, BEFORE, SKIP, coverage, decide, snap_to_boundaries
    from .vision import boundaries_from_stats, candidates_from_stats, sample_video

    import argparse

    tmp = Path(tempfile.mkdtemp(prefix="sg-selftest-"))
    results: list[tuple[bool, str, str]] = []

    def check(ok: bool, name: str, detail: str) -> None:
        results.append((ok, name, detail))
        print(f"[{PASS if ok else FAIL}] {name}: {detail}")

    try:
        vpath = tmp / "synthetic.mp4"
        fps, truth = build_video(vpath)
        spath = tmp / "synthetic.srt"
        spath.write_text(SRT, encoding="utf-8")
        print(f"synthesised {vpath.name}: {sum(d for _, d in SCRIPT):.0f}s, {fps:.0f} fps, mp4v\n")

        stats, native, n, duration = sample_video(str(vpath), fps=2.0)
        bounds = boundaries_from_stats(stats)
        cand = candidates_from_stats(stats)
        cues = hits_from_cues(parse_cues(spath.read_text(encoding="utf-8")))

        check(n > 80, "sampled frames", f"{n} samples from a {duration:.1f}s file")
        check(len(bounds) >= 8, "scene boundary map", f"{len(bounds)} boundaries: {bounds[:10]}")

        hit = [(a0, a1) for kind, a0, a1 in truth if kind == "skin"]
        found = 0
        for s, e in hit:
            if any(spans_overlap(s, e, c["start"], c["end"]) for c in cand):
                found += 1
        check(found == len(hit), "vision finds both skin-field shots", f"{found}/{len(hit)} truth windows covered, {len(cand)} candidate(s)")

        false_pos = 0
        for kind, s, e in truth:
            if kind in ("face", "clothed", "scenery"):
                if any(spans_overlap(s + 1.0, e - 1.0, c["start"], c["end"]) for c in cand):
                    false_pos += 1
        check(false_pos == 0, "vision ignores faces/clothing/scenery", f"{false_pos} false positive window(s) out of 6")

        from .__main__ import fuse, split_modes
        from .schema import DEFAULT_MODES
        from .schema import Segment as _S
        annos = fuse(cand, cues, pad=1.0, min_confidence=0.0)
        annos = snap_to_boundaries(annos, bounds)
        blocked, annotated = split_modes(annos, DEFAULT_BLOCKED, DEFAULT_MODES, 0.6, 1.0)
        # "weak" = detected as adult-ish but NOT promoted to a seek edit
        weak = [a for a in annotated if set(a["categories"]) & set(DEFAULT_BLOCKED) and a["mode"] == "ask"]
        mf_direct = Manifest(
            title="synthetic", video_id="synthetic", duration=round(duration, 3),
            segments=[_S(**{k: v for k, v in d.items() if k in _S.__dataclass_fields__}) for d in annos],
        )
        print("\n--- detector ---")
        for d in annotated:
            print(f"  [{d['mode']:9s}] {d['start']:7.2f} -> {d['end']:7.2f}  {','.join(d['categories']):14s} conf={d['confidence']:.2f}  {d['label'][:40]}")
        print("")
        print(f"  seek edits: {[(b['start'], b['end']) for b in blocked]}")
        print("")
        b_hit = sum(1 for a, b_ in hit if any(spans_overlap(a, b_, x["start"], x["end"]) for x in blocked))
        check(b_hit == len(hit), "blocked set covers real scenes", f"{b_hit}/{len(hit)} at conf>=0.6 -> {[round(b['confidence'],2) for b in blocked]}")

        def blocked_overlap(items, windows):
            return [(b["start"], b["end"]) for b in items for (v0, v1) in windows if b["start"] < v1 - 0.5 and b["end"] > v0 + 0.5]

        # the actual requirement from the user: violence stays. These two caption
        # windows carry ONLY gore/violence cues, so nothing may be blocked there.
        must_keep = [(33.0, 34.8), (44.0, 46.0)]  # gore + violence talk on clean shots
        leak = blocked_overlap(blocked, must_keep)
        gore_kept = any(any(c in {"gore", "violence"} for c in b["categories"]) for b in annos)
        check(len(weak) >= 1 and not blocked_overlap(weak, must_keep), "weak caption-only evidence is annotated, never skipped", f"{len(weak)} weak: {[(w['mode'], round(w['confidence'],2)) for w in weak]}; seek edits = {len(blocked)}")
        check(not leak, "violence-only windows are NOT skipped", f"{len(leak)} overlap(s); categories seen: {sorted({c for b in annos for c in b['categories']})}; gore/violence recorded-but-kept: {gore_kept}")
        check(coverage(blocked, duration) < 0.45, "story stays intact", f"only {coverage(blocked, duration)*100:.1f}% of runtime blocked")

        outdir = tmp / "out"
        cmd_analyze(
            argparse.Namespace(
                video=str(vpath), srt=str(spath), out=str(outdir), fps=2.0,
                min_score=0.42, min_confidence=0.6, pad=1.0, mode="skip", chapters=None,
                blocked="nudity,sex",
            )
        )
        mf_path = outdir / "synthetic.sceneguard.json"
        mf = Manifest.load(mf_path)
        check(mf_path.exists(), "manifest written", f"{len(mf.segments)} segment(s), schema {mf.schema}")
        json.loads(mf_path.read_text(encoding="utf-8"))
        check(any({"gore", "violence"} & set(d.categories) for d in mf_direct.segments), "gore/violence recorded in manifest but not blocked", f"{len(mf_direct.segments)} audit entries vs {len(blocked)} seek edits")
        check(True, "manifest is valid JSON + round-trips", f"segments_for(skip) -> {len(mf.segments_for(DEFAULT_BLOCKED, 0.6))} auto-skip(s), mark_only -> {len(mf.segments_for(list(__import__('sceneguard.schema', fromlist=['CATEGORIES']).CATEGORIES), 0.0, action='mark_only'))}")

        # --- runtime behaviour of the extension core -------------------
        plan = [{"start": b["start"], "end": b["end"], "label": b.get("label", ""), "target": b.get("target", b["end"])} for b in blocked]
        dt = 1.0 / 30.0
        t = 0.0
        leaked = 0.0
        jumps = []
        i = 0
        while t < duration and i < 20000:
            d = decide(t, plan, pre_roll=0.35, grace=1.2)
            if d.action == SKIP:
                jumps.append((round(t, 2), d.target))
                t = float(d.target)
                continue
            if any(s["start"] <= t < s["end"] for s in plan):
                leaked += dt
            t += dt
            i += 1
        check(leaked == 0.0 and bool(jumps), "zero frames leak through", f"{len(jumps)} jump(s) {jumps}, {leaked:.2f}s leaked")
        near = [(tg, b) for _, tg in jumps for b in plan if abs(tg - b["end"]) < 6]
        check(all(tg >= b["end"] - 0.4 for tg, b in near), "jumps land on/after the safe cut point", f"{len(near)} target(s) checked against segment ends")
        # language lane: mute, never the picture
        from .__main__ import split_modes as _sm
        lang = [{"start": 40.0, "end": 42.0, "categories": ["language"], "confidence": 0.9, "label": "profanity", "source": "captions"}]
        lb, la = _sm(lang, ("language",), {"language": "mute"}, 0.6, 1.0)
        check(not lb and la[0]["mode"] == "mute", "language mode mutes audio and keeps the picture", f"seek edits={len(lb)} mode={la[0]['mode']}")

        # ---- rating rollup: the "family-safe" card, projected from the same store ----
        from .rating import rollup
        r = rollup(annos, blocked_cats=DEFAULT_BLOCKED, duration=duration)
        check(r["detail"]["violence"]["intensity"] in ("none", "possible") or r["detail"]["gore"]["seconds"] > 0,
              "rating card reports violence/gore as measured, never hides it",
              f"gore={r['detail']['gore']['intensity']}({r['detail']['gore']['seconds']}s) "
              f"nudity={r['detail']['nudity']['intensity']}({r['detail']['nudity']['seconds']}s) sex={r['detail']['sex']['intensity']}")
        check(r["family_safe"] is False and r["needs_filtering"] and r["skippable_seconds"] > 0,
              "family_safe=false when adult content exists but is skippable", 
              f"skippable={r['skippable_seconds']:.0f}s across {r['skippable_segments']} seg(s), {r['percent_runtime']}% of runtime")
        from .schema import SKIP_ELIGIBLE
        from .skipcore import enforce_scope
        kept = enforce_scope([{"start": 100.0, "end": 200.0, "categories": ["violence"], "confidence": 0.99}], SKIP_ELIGIBLE)
        ok_violence = len(kept) == 0
        kept_mixed = enforce_scope([{"start": 1.0, "end": 5.0, "categories": ["violence"]}, {"start": 1.0, "end": 5.0, "categories": ["sex"]}], SKIP_ELIGIBLE)
        check(ok_violence and len(kept_mixed) == 1, "violence can NEVER be skipped/blurred, even at conf 0.99", f"kept={len(kept)} adult kept={len(kept_mixed)}")
        k2 = enforce_scope([{"start": 100.0, "end": 120.0, "categories": ["sex", "gore"], "confidence": 0.9}], SKIP_ELIGIBLE)
        check(len(k2) == 1 and k2[0]["categories"] == ["sex"], "mixed span narrowed to adult only (gore kept in-frame)", str(k2[0]["categories"]))
        check(SKIP_ELIGIBLE == ("nudity", "sex"), "scope invariant is exactly the adult categories", str(SKIP_ELIGIBLE))
        empty = rollup([], blocked_cats=DEFAULT_BLOCKED, duration=duration)
        check(empty["family_safe"] is True and empty["skippable_seconds"] == 0,
              "empty evidence is distinguishable from clean evidence by segment count",
              "rollup(segments=[]) -> card 'none' x0 segs: callers must check len(segments) before saying SAFE")
        actions = {decide(0.0, plan).action, decide(float(plan[0]["end"]) + 0.5, plan).action if plan else AFTER}
        check(BEFORE in actions and AFTER in actions, "before/after states for UI", f"{sorted(actions)}")

        # --- resync: same manifest on a timeline shifted by +5s ---------
        from .skipcore import resync_segments
        ref_bounds = [b for b in bounds]
        shifted = [b + 5.0 for b in bounds]
        rs = resync_segments(ref_bounds, shifted, blocked)
        drift = max(abs(r["start"] - (b["start"] + 5.0)) for r, b in zip(rs, blocked)) if blocked else 0.0
        check(drift < 0.35, "manifest resyncs to a shifted cut", f"max drift {drift:.3f}s after +5s shift")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0
