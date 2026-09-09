/*
 * SceneGuard core - pure functions, no DOM, no chrome APIs.
 *
 * This file is the JavaScript twin of sceneguard/skipcore.py. The content
 * script wires it to the <video> element; keeping it dependency-free is what
 * lets us test the shipping logic directly (`node extension/test-core.mjs`)
 * instead of only testing a mock of it.
 *
 * Hard constraint baked into these functions: we only ever call
 * video.currentTime = <seconds>. That is the same thing the user's remote does.
 * We never touch the stream, the licence server, or the decrypted frames -
 * which is the exact line between a legal filter and a DMCA s.1201 /
 * Copyright Act s.65A circumvention case.
 */

export const CATEGORIES = ["nudity", "sex", "language", "gore", "violence", "drugs", "horror"];
/* HARD INVARIANT - mirror of sceneguard/schema.py:SKIP_ELIGIBLE.
 * These are the ONLY categories allowed to remove or obscure picture.
 * violence / gore / language / drugs / horror are detected, muted or marked,
 * never skipped and never blurred. */
export const SKIP_ELIGIBLE = ["nudity", "sex"];
export const BEFORE = "before";
export const SKIP = "skip";
export const AFTER = "after";

/** union of overlapping spans, optionally without bleeding categories */
export function mergeOverlapping(segments, gap = 0, byCategory = false) {
  if (!segments.length) return [];
  const ordered = [...segments].sort((a, b) => a.start - b.start || a.end - b.end);
  const out = [];
  for (const seg of ordered) {
    const last = out[out.length - 1];
    const sameSig =
      !byCategory ||
      (last && last.categories.slice().sort().join() === seg.categories.slice().sort().join());
    if (last && seg.start - last.end <= gap && sameSig) {
      last.end = Math.max(last.end, seg.end);
      const seen = new Set(last.categories);
      for (const c of seg.categories) if (!seen.has(c)) last.categories.push(c);
      last.confidence = Math.max(last.confidence, seg.confidence);
    } else {
      out.push({ ...seg, categories: [...seg.categories] });
    }
  }
  return out;
}

/** resolve a manifest against one viewer's filters -> the seek edits to apply */
export function resolveManifest(segments, opts) {
  const { blocked = ["nudity", "sex"], modes = {}, minConfidence = 0.6, pad = 1.0 } = opts || {};
  const blocking = [];
  const annotated = [];
  for (const s of segments || []) {
    const matched = (s.categories || []).filter((c) => blocked.includes(c)).sort();
    let want = s.mode || (matched.length ? modes[matched[0]] || "skip" : "mark_only");
    const conf = s.confidence == null ? 0.5 : s.confidence;
    // confidence gate sits in front of the mode: weak evidence never removes picture
    let mode = conf >= minConfidence ? want : want === "skip" ? "ask" : want;
    const item = { ...s, mode, categories: [...(s.categories || [])] };
    annotated.push(item);
    if (mode === "skip") blocking.push(item);
  }
  const spans = enforceScope(
    mergeOverlapping(blocking, pad, true).map((s) => ({
      ...s,
      start: Math.max(0, s.start - (s.pre == null ? pad : s.pre)),
      target: s.target == null ? s.end + (s.post == null ? pad : s.post) : s.target,
    }))
  ).map((s) => ({ ...s, mode: "skip" }));
  return {
    spans: spans.sort((a, b) => a.start - b.start),
    annotated,
    muted: muteSpans(annotated, { modes, blocked: Object.keys(modes), minConfidence }),
  };
}

/** the 50 ms tick */
export function decide(t, spans, opts) {
  const { preRoll = 0.35, grace = 1.2 } = opts || {};
  for (let i = 0; i < spans.length; i++) {
    const s = spans[i];
    const start = s.start + (s.pre == null ? 0 : 0);
    const end = s.end;
    if (t < start - preRoll) return { action: BEFORE, index: i };
    if (t >= start - preRoll && t < end) {
      return { action: SKIP, target: s.target == null ? end : s.target, label: s.label || "", index: i };
    }
    if (t >= end && t < end + grace) return { action: AFTER, index: i };
  }
  return { action: AFTER, index: -1 };
}

export function countdown(t, spans) {
  const next = spans.find((s) => s.start >= t);
  return next ? { seconds: Math.max(0, next.start - t), label: next.label || "" } : null;
}

/** last gate before anything can cut picture - see SKIP_ELIGIBLE */
export function enforceScope(spans, eligible = SKIP_ELIGIBLE) {
  const allowed = new Set(eligible);
  const kept = [];
  for (const s of spans) {
    const cats = (s.categories || []).filter((c) => allowed.has(c));
    if (!cats.length) continue; // e.g. a hand-edited manifest tagging a fight scene as adult
    kept.push({ ...s, categories: cats.sort() });
  }
  return mergeOverlapping(kept, 0, true);
}

/**
 * Audio-only lane. `modes` can mix actions across categories, so "mute the
 * gaali, skip the sex scene, keep the fight" is one config, not three products.
 * A mute span never removes picture - that is the whole point: it is safe
 * against false positives in a way a skip is not.
 */
export function muteSpans(segments, opts) {
  const { modes = {}, blocked = [], minConfidence = 0.6 } = opts || {};
  return (segments || [])
    .filter(
      (s) =>
        (s.confidence == null ? 0.5 : s.confidence) >= minConfidence &&
        (s.categories || []).some((c) => blocked.includes(c) && (s.mode || modes[c]) === "mute")
    )
    .map((s) => ({ start: s.start, end: s.end }));
}

export { BEFORE as BEFORE_STATE };
