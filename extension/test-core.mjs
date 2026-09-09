import assert from "node:assert/strict";
import { decide, mergeOverlapping, muteSpans, resolveManifest, AFTER, BEFORE, SKIP } from "./core.mjs";
import { readFileSync } from "node:fs";

// These cases are the JS mirror of the 15 Python checks, so the browser build
// and the offline analyzer cannot drift apart.
const manifest = JSON.parse(
  readFileSync(new URL("./fixtures/synthetic.sceneguard.json", import.meta.url), "utf8")
);

let pass = 0;
const ok = (name, cond, detail = "") => {
  assert.ok(cond, `${name} -- ${detail}`);
  pass++;
  console.log(`[PASS] ${name}${detail ? ": " + detail : ""}`);
};

const blocked = ["nudity", "sex"];
const modes = { nudity: "skip", sex: "skip", gore: "mark_only", violence: "mark_only", language: "mute" };
const { spans, annotated } = resolveManifest(manifest.segments, { blocked, modes, minConfidence: 0.6, pad: 0 });

ok("extension loads a real SceneGuard manifest", spans.length === 2, `${spans.length} seek spans from ${annotated.length} annotations`);
ok(
  "violence/gore entries are present but produce no seek edit",
  annotated.some((a) => a.categories.includes("gore") || a.categories.includes("violence")) &&
    !spans.some((s) => s.categories.length === 1 && (s.categories[0] === "gore" || s.categories[0] === "violence")),
  `gore spans = ${spans.filter((s) => s.categories.includes("gore")).length}`
);
ok("weak caption-only evidence degrades to 'ask'", annotated.filter((a) => a.mode === "ask").length >= 1, `${annotated.filter((a) => a.mode === "ask").length} ask-mode entries`);

// zero-frame-leak simulation at 30 fps, identical contract to the Python test
let t = 0;
let leaked = 0;
let jumps = 0;
let guard = 0;
while (t < manifest.duration && guard++ < 100000) {
  const d = decide(t, spans, { preRoll: 0.35, grace: 1.2 });
  if (d.action === SKIP) {
    jumps++;
    t = d.target;
    continue;
  }
  if (spans.some((s) => t >= s.start && t < s.end)) leaked += 1 / 30;
  t += 1 / 30;
}
ok("no blocked frame is ever painted", leaked === 0 && jumps === spans.length, `${jumps} jump(s), ${leaked.toFixed(2)}s leaked`);
ok("state machine exposes before/after for the UI", decide(0, spans).action === BEFORE && decide(manifest.duration - 1, spans).action === AFTER);

ok("merge unions overlaps and keeps gaps", mergeOverlapping([{ start: 0, end: 5, categories: ["sex"] }, { start: 5.5, end: 8, categories: ["sex"] }], 1).length === 1);
ok(
  "byCategory prevents gore being dragged into a skip",
  (() => {
    const m = mergeOverlapping(
      [
        { start: 0, end: 5, categories: ["nudity"] },
        { start: 5.5, end: 8, categories: ["gore"] },
      ],
      1,
      true
    );
    return m.length === 2;
  })()
);
ok("mute lane works independently of skip lane", muteSpans([{ start: 10, end: 12, categories: ["language"], confidence: 0.9 }], { blocked: ["language"], modes: { language: "mute" } }).length === 1);
ok("countdown targets the next skip", decide(0, spans).index === 0 && spans[0].start > 0);

// the config from the user's table: skip the sex scene, MUTE the gaali, KEEP the fight
const mixed = resolveManifest(manifest.segments, {
  blocked: ["nudity", "sex", "language", "gore"],
  modes: { nudity: "skip", sex: "skip", language: "mute", gore: "mark_only" },
  minConfidence: 0.6,
  pad: 0,
});
ok("one config mixes skip + mute + mark_only across categories",
  mixed.spans.length === 2 && "muted" in mixed,
  `skip=${mixed.spans.length} mute=${mixed.muted.length} mark_only=${mixed.annotated.filter((a) => a.mode === "mark_only").length}`);
ok("language evidence mutes without removing picture",
  (() => {
    const m = resolveManifest([{ start: 10, end: 12, categories: ["language"], confidence: 0.9 }], {
      blocked: ["language"], modes: { language: "mute" }, minConfidence: 0.6, pad: 0,
    });
    return m.spans.length === 0 && m.muted.length === 1;
  })());
ok("mute lane ignores low-confidence evidence too",
  resolveManifest([{ start: 10, end: 12, categories: ["language"], confidence: 0.3 }], {
    blocked: ["language"], modes: { language: "mute" }, minConfidence: 0.6, pad: 0,
  }).muted.length === 0);

// the product invariant: violence/gore/language can never cut picture
import { enforceScope, SKIP_ELIGIBLE } from "./core.mjs";
ok("SKIP_ELIGIBLE is exactly the adult categories", SKIP_ELIGIBLE.join() === "nudity,sex", SKIP_ELIGIBLE.join());
ok("a manifest that tags a fight scene as violence gets no seek edit",
  enforceScope([{ start: 100, end: 200, categories: ["violence"], mode: "skip" }]).length === 0);
ok("blur/skip on an adult category survives the gate",
  enforceScope([{ start: 100, end: 120, categories: ["nudity", "gore"], mode: "blur" }]).length === 1 &&
  enforceScope([{ start: 100, end: 120, categories: ["nudity", "gore"] }])[0].categories.join() === "nudity");
ok("mixed span is narrowed, not dropped (adult part still skipped, gore kept)",
  (() => { const r = enforceScope([{ start: 10, end: 20, categories: ["sex", "gore"] }]); return r.length === 1 && !r[0].categories.includes("gore"); })());

console.log(`\n${pass}/${pass} JS checks passed (mirrors the Python reference)`);
