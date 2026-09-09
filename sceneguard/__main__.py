"""
SceneGuard CLI.

    python -m sceneguard selftest                          # no ffmpeg, no deps to install
    python -m sceneguard analyze movie.mp4 --srt movie.srt --out manifest/
    python -m sceneguard mark movie.mp4 --start 1183.2 --end 1199.0 --cat sex

`analyze` only ever touches files the user lawfully holds and can decode:
local video, purchased MP4s, home video. It never touches a DRM stream.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from . import skipcore as sc
from .lexicon import SKIP_CAPABLE, hits_from_cues, parse_cues
from .schema import DEFAULT_BLOCKED, DEFAULT_MODES, MODES, SKIP_ELIGIBLE, Manifest, Segment


# --------------------------------------------------------------------- fusion
def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return 0.0 if union <= 0 else inter / union


def fuse(vision: list[dict], cues, *, pad: float = 1.0, min_confidence: float = 0.35) -> list[dict]:
    """Two-lane fusion -> (annotations, blocking).

    Lane 1 (vision) proposes WHERE. Lane 2 (captions) proposes WHAT and also
    covers films with no usable subtitles. Rules that matter:

      * a vision hit is only promoted to a real skip if captions agree (IoU>0.15)
        or its own confidence clears 0.55 - this is the false-positive brake;
      * caption evidence for sex/nudity near a vision hit raises confidence and
        may re-tag 'nudity' -> 'sex' (parents asked for the stronger tag);
      * caption-only segments are kept but capped at 0.55 confidence, because a
        line of dialogue about a bedroom is not a scene in one.
    """
    segs: list[dict] = []
    for v in vision:
        span = (v["start"], v["end"])
        near = [h for h in cues if iou(span, (h.start, h.end)) > 0.02 or max(h.start, v["start"]) <= min(h.end, v["end"])]
        cat_scores: dict[str, float] = {}
        for h in near:
            for cat, val in h.scores.items():
                cat_scores[cat] = max(cat_scores.get(cat, 0.0), val)
        cats = list(v["categories"])
        conf = float(v["confidence"])
        strong = [c for c, s in cat_scores.items() if s >= 0.45]
        if strong:
            # soft-OR corroboration: agreement between lanes buys confidence
            corrob = 1.0 - math.prod(1.0 - min(0.9, cat_scores[c]) * 0.55 for c in strong)
            conf = min(0.98, conf + 0.45 * corrob)
            if "sex" in strong and "nudity" in cats:
                cats = ["sex"]
        segs.append({**v, "categories": cats, "confidence": round(conf, 3), "label": v["label"]})

    for h in cues:
        for cat, val in h.scores.items():
            if val < 0.45:
                continue
            if any(iou((h.start, h.end), (s["start"], s["end"])) > 0.05 for s in segs):
                continue  # already covered by a vision lane segment
            # Skip-capable cues get a conservative capped confidence (dialogue
            # about a bedroom is not a scene in one). Non-skip-capable cues are
            # still recorded: the manifest must be honest about gore/violence
            # it deliberately KEPT, otherwise parents cannot audit it.
            cap = 0.55 if cat in SKIP_CAPABLE else 0.45
            segs.append(
                {
                    "start": h.start,
                    "end": h.end,
                    "categories": [cat],
                    "confidence": round(min(cap, 0.25 + 0.4 * val), 3),
                    "label": f"caption-only evidence: “{h.text[:60]}”",
                    "source": "captions",
                }
            )

    # Merge evidence into ANNOTATIONS only. Every strong, well-supported span in
    # a skip-capable category lands there. Nothing is padded yet, and nothing is
    # tagged "blocking" yet.
    annos = sc.merge_overlapping(segs, gap=2.0, by_category=True)
    return [a for a in annos if float(a["confidence"]) >= 0.0]


def derive_blocking(annos: list[dict], blocked_cats, min_confidence: float, pad: float) -> list[dict]:  # noqa: ARG001
    """Promote annotations to seek edits: strong + skip-capable + merged.

    The annotation itself is never mutated. Padding becomes a *derived field*
    (`pre`/`post`), because the manifest has to stay a faithful record of what
    was detected, and because padding-in-the-manifest double-counts when the
    player also applies a pre-roll. Consumer decides how aggressive to be.
    """
    strong = [dict(a) for a in annos if set(a["categories"]) & set(blocked_cats)]
    strong = sc.merge_overlapping(strong, gap=max(0.0, pad), by_category=True)
    for s in strong:
        s["pre"] = round(pad, 3)
        s["post"] = round(pad, 3)
    return strong


def split_modes(annos: list[dict], blocked_cats, modes: dict, min_confidence: float, pad: float):
    """Resolve every annotation against the viewer's per-category modes.

    Returns (blocking, annotated). Categories whose mode is 'mute' or
    'mark_only' can never produce a seek edit however confident we are - that is
    the entire trick behind "my parents can watch the violence, not this".
    A weak annotation stays weak forever: no amount of proximity to a strong one
    promotes it, so a stray line of dialogue can never widen what gets cut.
    """
    annotated, blocking = [], []
    for a in annos:
        matched = sorted(set(a["categories"]) & set(blocked_cats))
        want = a.get("mode") or (modes.get(matched[0], "mark_only") if matched else "mark_only")
        # Confidence gate sits in FRONT of the mode, not behind it: weak evidence
        # can never earn the right to remove picture, no matter which category the
        # viewer chose to block. It degrades to a marker the user can act on.
        eff = want if a["confidence"] >= min_confidence else ("ask" if want == "skip" else want)
        item = dict(a)
        item["mode"] = eff
        if want in ("skip", "mute") and a["confidence"] < min_confidence:
            item["note"] = f"below confidence gate ({a['confidence']:.2f} < {min_confidence:.2f}); not auto-applied"
        annotated.append(item)
    blocking = derive_blocking([a for a in annotated if a["mode"] == "skip"], blocked_cats, min_confidence, pad)
    # last gate, applies even to hand-edited manifests and future configs
    kept = sc.enforce_scope(blocking, SKIP_ELIGIBLE)
    refused = sc.demoted(blocking, kept)
    for d in refused:
        for a in annotated:
            if abs(a["start"] - d["start"]) < 0.01 and abs(a["end"] - d["end"]) < 0.01:
                a["mode"] = "mark_only"
                a["note"] = "out of scope: violence/gore/language are never skipped or blurred"
    return kept, annotated
def write_mpv_chapters(path: Path, title: str, blocked: list[dict], duration: float | None = None) -> None:
    """Chapters file any libmpv front-end reads. Cheap way to make a manifest
    usable in existing players on day one instead of waiting for our own app."""
    marks = [0.0]
    for s in blocked:
        marks += [float(s["start"]), float(s["end"])]
    if duration:  # players choke on a chapter marker past the last frame
        marks = [m for m in marks if m < duration]
    marks = sorted({round(m, 2) for m in marks})
    labels = []
    t = 0.0
    for m in marks:
        kind = "SKIP:" if any(m >= s["start"] - 0.01 and m < s["end"] for s in blocked) else ""
        labels.append(f"{kind}{m - t:.1f}s")
        t = m
    lines = ["; mpv chapters - generated by SceneGuard", f"; title: {title}", "[Chapters]"]
    for i, m in enumerate(marks):
        hh, rem = divmod(int(round(m)), 3600)
        mm, ss = divmod(rem, 60)
        lines.append(f";{hh:02d}:{mm:02d}:{ss:05.2f} -- {labels[i] if i < len(labels) else ''}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(path: Path, title: str, blocked: list[dict], duration: float, notes: list[str]) -> None:
    rows = "".join(
        f"<tr><td>{s['start']:.1f}s</td><td>{s['end']:.1f}s</td><td>{s['end']-s['start']:.1f}s</td>"
        f"<td>{', '.join(s['categories'])}</td><td>{s['confidence']:.2f}</td><td>{s.get('label','')}</td></tr>"
        for s in blocked
    ) or "<tr><td colspan=6>nothing blocked at this threshold - good</td></tr>"
    cov = sc.coverage(blocked, duration) * 100 if duration else 0
    path.write_text(
        f"<meta charset='utf-8'><title>{title} - SceneGuard report</title>"
        f"<h2>{title}</h2>"
        f"<p>duration {duration:.1f}s &middot; blocked {len(blocked)} segment(s) &middot; {cov:.2f}% of runtime</p>"
        f"<table border=1 cellpadding=6 style='border-collapse:collapse;font:14px system-ui'>"
        f"<tr><th>start</th><th>end</th><th>len</th><th>cats</th><th>conf</th><th>why</th></tr>{rows}</table>"
        f"<h3>notes</h3><ul>{''.join(f'<li>{n}</li>' for n in notes)}</ul>",
        encoding="utf-8",
    )


# ------------------------------------------------------------------ commands
def cmd_analyze(a: argparse.Namespace) -> int:
    from .vision import boundaries_from_stats, candidates_from_stats, sample_video

    stats, native, n, duration = sample_video(a.video, fps=a.fps)
    bounds = boundaries_from_stats(stats)
    vision = candidates_from_stats(stats, min_score=a.min_score)
    cues = []
    if a.srt and Path(a.srt).exists():
        cues = hits_from_cues(parse_cues(Path(a.srt).read_text(encoding="utf-8", errors="ignore")))
    annotations = fuse(vision, cues, pad=a.pad, min_confidence=a.min_confidence)

    # Resync vs. snap: resync is for applying somebody else's manifest to your
    # cut; snapping is for the manifest you just produced from this very file.
    if a.chapters:
        ref = json.loads(Path(a.chapters).read_text(encoding="utf-8"))
        annotations = sc.resync_segments(ref["boundaries"], bounds, annotations)
    else:
        annotations = sc.snap_to_boundaries(annotations, bounds)

    illegal = [c.strip() for c in a.blocked.split(",") if a.mode in ("skip", "blur") and c.strip() not in SKIP_ELIGIBLE]
    if illegal:
        # refuse loudly instead of silently doing the wrong thing to someone's
        # family movie night, so the invariant is obvious at the CLI, not buried
        raise SystemExit(
            f"refusing: --blocked {{{','.join(illegal)}}} with --mode {a.mode}. "
            f"Only {list(SKIP_ELIGIBLE)} may be skipped or blurred; violence, gore and "
            f"language are detect/mute/mark only. Use --mode mute or --mode mark_only."
        )
    modes = dict(DEFAULT_MODES)
    for c in a.blocked.split(","):
        modes[c.strip()] = a.mode
    blocking, annotations = split_modes(
        annotations, tuple(c.strip() for c in a.blocked.split(",")), modes, a.min_confidence, a.pad
    )

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(a.video).stem
    fields = set(Segment.__dataclass_fields__)
    mf = Manifest(
        title=stem,
        video_id=stem,
        duration=round(duration, 3),
        generated_by="sceneguard-cli",
        segments=[Segment(**{k: v for k, v in s.items() if k in fields}) for s in annotations],
    )
    mf_path = mf.save(out / f"{stem}.sceneguard.json")
    write_mpv_chapters(out / f"{stem}.chapters", stem, blocking, duration)
    write_report(
        out / f"{stem}.report.html",
        stem,
        blocking,
        duration,
        [
            f"sampled {n} frames at {a.fps} fps from a {native:.2f} fps file",
            f"vision candidates: {len(vision)} | caption cues scored: {len(cues)}",
            f"auto-skip spans: {len(blocking)} | timeline annotations: {len(annotations)}",
            f"modes: {modes}",
            "proxy classifier - corroboration required; read vision.py before shipping this as the product",
        ],
    )
    print(str(mf_path))
    print(f"  annotations={len(annotations)} auto_skip={len(blocking)} coverage={sc.coverage(blocking, duration)*100:.2f}%")
    for s in annotations:
        print(f"  [{s['mode']:9s}] {s['start']:8.2f} -> {s['end']:8.2f}  {','.join(s['categories']):14s} conf={s['confidence']:.2f}  {s['label'][:38]}")
    print(f"  chapters: {out / (stem + '.chapters')}")
    print(f"  report:   {out / (stem + '.report.html')}")
    return 0


def _seg_to_dict(s: Segment) -> dict:
    return {
        "start": s.start,
        "end": s.end,
        "categories": list(s.categories),
        "confidence": s.confidence,
        "label": s.label,
        "source": s.source,
    }


def cmd_mark(a: argparse.Namespace) -> int:
    """Community/manual timestamps. This is the path to actual accuracy: the
    only detector that never misses a scene is a human who saw it."""
    stem = Path(a.video).stem if a.video else a.title
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.sceneguard.json"
    mf = Manifest.load(path) if path.exists() else Manifest(title=stem, video_id=stem, generated_by="manual")
    cats = [c.strip() for c in a.cat.split(",") if c.strip()]
    new = Segment(
        start=a.start,
        end=a.end,
        categories=cats,
        confidence=1.0 if a.verified else 0.6,
        label=a.note or "manually marked",
        source="verified_user" if a.verified else "community",
    )
    # union with what's already there, then merge overlapping contributions so
    # two neighbours marking the same scene collapse into one verified skip.
    dicts = sc.merge_overlapping([_seg_to_dict(s) for s in list(mf.segments) + [new]], gap=1.0)
    mf.segments = [
        Segment(
            start=float(d["start"]),
            end=float(d["end"]),
            categories=list(d.get("categories", [])),
            confidence=float(d.get("confidence", 0.5)),
            label=d.get("label", ""),
            source=d.get("source", "community"),
        )
        for d in dicts
    ]
    mf.save(path)
    print(f"marked {a.start}-{a.end} {cats} -> {path} ({len(mf.segments)} segment(s))")
    return 0



def cmd_rate(a: argparse.Namespace) -> int:
    """Project the evidence store into the card a parent reads.

    No model, no new data. If a title has no evidence at all we say "no data",
    we never say "safe" - a false 'safe' is the one output that ends a product.
    """
    from .rating import render_card, rollup

    mf = Manifest.load(a.manifest)
    r = rollup(
        [{k: v for k, v in _seg_to_dict(s).items()} for s in mf.segments],
        blocked_cats=tuple(c.strip() for c in a.blocked.split(",")),
        duration=mf.duration,
    )
    if not mf.segments:
        r["card_note"] = "no evidence on file"
        verdict = "UNKNOWN - no data (NOT 'safe')"
    else:
        verdict = "family-safe WITH filtering on" if r["needs_filtering"] else "family-safe as-is"
    print(f"{mf.title}  ->  {verdict}")
    print(f"  segments={len(mf.segments)} skippable={r['skippable_seconds']:.0f}s ({r['skippable_segments']} segs)"
          + (f" = {r['percent_runtime']}% of runtime" if r['percent_runtime'] is not None else ""))
    for cat, v in r["detail"].items():
        if v["segments"]:
            print(f"  {cat:10s} {v['intensity']:9s} {v['seconds']:7.1f}s  max_conf={v['max_confidence']:.2f}")
    if r["unconfirmed"]:
        print(f"  unconfirmed: {r['unconfirmed']} (scored 'possible', not 'none')")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    card = out / f"{Path(a.manifest).stem.replace('.sceneguard','')}.card.html"
    card.write_text(render_card(mf.title, r, r.get("card_note", "")), encoding="utf-8")
    (out / f"{Path(a.manifest).stem.replace('.sceneguard','')}.rating.json").write_text(json.dumps(r, indent=2), encoding="utf-8")
    print(f"  card: {card}")
    return 0


def cmd_selftest(a: argparse.Namespace) -> int:
    from .selftest import run
    return run()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sceneguard", description="Scene-level adult-content skipper for DRM-free video")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("analyze", help="detect + write manifest/chapters/report")
    s.add_argument("video")
    s.add_argument("--srt", default=None, help="caption track to fuse in")
    s.add_argument("--out", default=".sceneguard")
    s.add_argument("--fps", type=float, default=2.0, help="sampling rate (default 2)")
    s.add_argument("--min-score", type=float, default=0.42, help="vision proxy threshold")
    s.add_argument("--min-confidence", type=float, default=0.6,
                   help="auto-skip threshold; weak evidence is recorded but only marked (default 0.6)")
    s.add_argument("--pad", type=float, default=1.0, help="seconds to widen each segment")
    s.add_argument("--chapters", default=None, help="reference boundary JSON to resync against")
    s.add_argument("--blocked", default=",".join(DEFAULT_BLOCKED),
                   help="categories this viewer acts on; only %s can ever be skipped/blurred" % (",".join(SKIP_ELIGIBLE),))
    s.add_argument("--mode", default="skip", choices=list(__import__("sceneguard.schema", fromlist=["MODES"]).MODES),
                   help="what to do with blocked categories: skip | ask | mute | mark_only")
    s.set_defaults(fn=cmd_analyze)

    m = sub.add_parser("mark", help="add a manual/community timestamp")
    m.add_argument("video", nargs="?", default="")
    m.add_argument("--title", default="untitled")
    m.add_argument("--start", type=float, required=True)
    m.add_argument("--end", type=float, required=True)
    m.add_argument("--cat", default="sex")
    m.add_argument("--note", default="")
    m.add_argument("--verified", action="store_true")
    m.add_argument("--out", default=".sceneguard")
    m.set_defaults(fn=cmd_mark)

    rt = sub.add_parser("rate", help="roll a manifest up into a family-safe rating card")
    rt.add_argument("manifest")
    rt.add_argument("--blocked", default=",".join(DEFAULT_BLOCKED))
    rt.add_argument("--out", default=".sceneguard")
    rt.set_defaults(fn=cmd_rate)

    t = sub.add_parser("selftest", help="synthesize a test video and run the whole pipeline")
    t.set_defaults(fn=cmd_selftest)
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
