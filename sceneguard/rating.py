"""
Family-safe rating rollup.

Your "FamilyWatch" card (Violence: High / Nudity: None / Family-safe: Yes) is
NOT a computer-vision problem. It is a rollup over whatever evidence we already
collected. That matters enormously for cost: a team that treats the rating as a
separate ML feature builds two products; a team that treats it as a projection
of one evidence store builds one.

Two rules that keep this honest:

1. The rollup reports what was DETECTED, and always says so. "Nudity: None" is
   only ever printed as "Nudity: none detected (caption coverage 82%)" when we
   actually looked. An unscored title must never render as a safe title - that
   is how a family gets burned and how you lose the trust that is the entire
   product.

2. Intensity is derived from *durations and corroboration*, not from a platform
   age rating. Two U/A 13+ Marathi films can differ by 10x in skin-screen
   minutes. Parents are buying the difference, not the label.
"""

from __future__ import annotations

from .schema import CATEGORIES

#: seconds of flagged material -> intensity bucket. Tunable per market.
BANDS = ((0.0, "none"), (15.0, "low"), (90.0, "medium"), (float("inf"), "high"))


def _band(seconds: float) -> str:
    band = "none"
    for hi, name in BANDS:
        if seconds <= hi:
            return name if name != "none" or seconds == 0 else band
        band = name
    return "high"


def rollup(segments: list[dict], *, blocked_cats=("nudity", "sex"), duration: float | None = None) -> dict:
    """Per-category intensity + a family-safe verdict + what we could NOT see."""
    per: dict[str, dict] = {c: {"seconds": 0.0, "segments": 0, "max_confidence": 0.0} for c in CATEGORIES}
    for s in segments:
        span = max(0.0, float(s["end"]) - float(s["start"]))
        for c in s.get("categories", []):
            if c not in per:
                continue
            per[c]["seconds"] += span
            per[c]["segments"] += 1
            per[c]["max_confidence"] = max(per[c]["max_confidence"], float(s.get("confidence", 0.5)))

    card: dict[str, str] = {}
    for c, v in per.items():
        band = _band(round(v["seconds"], 1))
        # unconfirmed evidence must not read as "none"
        if band != "none" and v["max_confidence"] < 0.5:
            band = "possible"
        card[c] = band
        v["seconds"] = round(v["seconds"], 1)
        v["intensity"] = band

    blocked_secs = sum(per[c]["seconds"] for c in blocked_cats)
    weak = [c for c, v in per.items() if v["segments"] and v["max_confidence"] < 0.5]
    return {
        "card": card,
        "detail": per,
        "family_safe": blocked_secs == 0 and not weak,
        "skippable_seconds": round(blocked_secs, 1),
        "skippable_segments": sum(per[c]["segments"] for c in blocked_cats),
        "needs_filtering": blocked_secs > 0,
        "percent_runtime": round(100.0 * blocked_secs / duration, 2) if duration else None,
        "unconfirmed": weak,
        "note": "based on {} tagged segment(s); 'none' means none detected, not none present".format(len(segments)),
    }


def render_card(title: str, r: dict, coverage_note: str = "") -> str:
    """The card a parent reads. Plain HTML, no stylesheet, works anywhere."""
    dot = {"none": "🟢", "low": "🟡", "medium": "🟠", "high": "🔴", "possible": "⚪️"}
    rows = "".join(
        f"<tr><td>{c}</td><td>{dot.get(v['intensity'],'⚪️')} {v['intensity']}</td>"
        f"<td>{v['seconds']:.0f}s in {v['segments']} segment(s)</td>"
        f"<td>{'auto-skips' if c in ('nudity','sex') else 'kept, marked only'}</td></tr>"
        for c, v in r["detail"].items()
        if v["segments"] or c in ("nudity", "sex", "violence", "gore", "language")
    )
    verdict = "SAFE as-is" if r["family_safe"] else ("FILTERED = family-safe" if r["needs_filtering"] else "SAFE as-is")
    return (
        f"<meta charset='utf-8'><div style='font:15px system-ui;max-width:640px;padding:20px'>"
        f"<h3 style='margin:0 0 4px'>{title}</h3>"
        f"<div style='color:#666;margin-bottom:12px'>{verdict} &middot; {r['note']}</div>"
        f"<table style='border-collapse:collapse;width:100%'><tr style='text-align:left;color:#777'>"
        f"<th>category</th><th>intensity</th><th>what we saw</th><th>with SceneGuard on</th></tr>{rows}</table>"
        f"<div style='margin-top:12px;color:#8a6d1e'>{coverage_note}</div></div>"
    )
