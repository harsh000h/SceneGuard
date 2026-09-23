/*
 * SceneGuard content script.
 *
 * Scope, stated plainly so nobody is surprised later:
 *   WE DO:  read the title from the page, fetch a timestamp manifest, and call
 *           video.currentTime to seek past spans the viewer blocked.
 *   WE DO NOT: decrypt anything, capture frames, download segments, re-encode,
 *           or host copies. On a DRM title the frames are not ours to look at,
 *           so "AI scans your Netflix stream" is not a feature we can build -
 *           it is the lawsuit. The manifest is the product.
 *
 * Works on desktop Chrome (Netflix / Prime / JioHotstar / Sony LIV / Zee5 all
 * use an HTML5 <video>). It does NOT work on Smart TV, Fire TV, or the mobile
 * apps, because those are closed environments - which is also why ClearPlay
 * says outright they will not build TV apps. Ship a laptop-on-HDMI workflow,
 * or a companion box, not a TV app.
 */

const { decide, countdown, muteSpans, resolveManifest } = await import(
  chrome.runtime.getURL("core.mjs"));

const TICK_MS = 50;
const KEY = "sceneguard:prefs";

const DEFAULT_PREFS = {
  blocked: ["nudity", "sex"],
  modes: {
    nudity: "skip",
    sex: "skip",
    language: "mute",
    gore: "mark_only",
    violence: "mark_only",
    drugs: "mark_only",
    horror: "mark_only",
  },
  minConfidence: 0.6,
  pad: 1.0,
  api: "http://localhost:8788",
  enabled: true,
};

const getPrefs = async () => ({
  ...DEFAULT_PREFS,
  ...((await chrome.storage?.local.get(KEY))?.[KEY] || {}),
});

// ---------------------------------------------------------------- title match
/**
 * The genuinely hard engineering problem, harder than detection: one title has
 * a Netflix id, a Prime id, a hotstar id, and no two platforms agree on whether
 * "S02E05" includes the recap. Match on a normalised string, then let the
 * runtime correct for drift with the local scene map (see skipcore.resync).
 */
function identify() {
  const host = location.hostname.replace(/^www\./, "");
  const url = new URL(location.href);
  const q = url.searchParams;
  let title =
    document.querySelector('h3[data-uia="title"]')?.textContent ||
    document.querySelector('[role="button"] h3')?.textContent ||
    q.get("title") ||
    document.title.split(/[|–-]/)[0];
  title = (title || "").trim().replace(/\s*\(?\d{4}\)?\s*$/, "");
  const season = Number(q.get("s") || q.get("season") || (url.pathname.match(/season-(\d+)/) || [])[1] || 0) || null;
  const episode = Number(q.get("e") || q.get("episode") || (url.pathname.match(/episode-(\d+)/) || [])[1] || 0) || null;
  const filmId = q.get("movieId") || q.get("vi") || url.pathname.match(/\/title\/(tt\d+)/)?.[1] || null;
  return { host, title, season, episode, filmId, key: `${host}|${title}|S${season ?? "-"}E${episode ?? "-"}` };
}

async function fetchManifest(id) {
  const { api } = await getPrefs();
  const params = new URLSearchParams({ title: id.title, host: id.host });
  if (id.season) params.set("season", id.season);
  if (id.episode) params.set("episode", id.episode);
  if (id.filmId) params.set("film_id", id.filmId);
  try {
    const r = await fetch(`${api}/v1/manifest?${params}`, { credentials: "omit" });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null; // offline / no coverage: degrade to "no filtering", never to a broken player
  }
}

// ------------------------------------------------------------------ overlay UI
function mount(video) {
  const box = document.createElement("div");
  box.style.cssText =
    "position:absolute;left:16px;bottom:96px;z-index:2147483647;display:flex;gap:8px;" +
    "font:500 13px/1.2 system-ui,sans-serif;pointer-events:auto";
  box.innerHTML = `
    <button data-act="skip" style="display:none;background:rgba(15,15,18,.86);color:#fff;border:1px solid #4b4b55;border-radius:8px;padding:7px 11px;cursor:pointer">
      Skip <span data-why></span> · <kbd>。</kbd>
    </button>
    <button data-act="mark" title="Mark the start/end of a scene ( , then . )"
      style="background:rgba(15,15,18,.72);color:#ddd;border:1px solid #3a3a44;border-radius:8px;padding:7px 11px;cursor:pointer">
      Mark scene · <kbd>,</kbd>
    </button>
    <span data-state style="color:#9ad19a;align-self:center"></span>`;
  const host = video.parentElement || document.body;
  host.style.position = host.style.position || "relative";
  host.appendChild(box);
  return box;
}

// --------------------------------------------------------------------- runtime
let state = { video: null, spans: [], muted: [], box: null, markStart: null, lastJumpAt: 0 };

async function attach(video) {
  const prefs = await getPrefs();
  if (!prefs.enabled) return;
  const id = identify();
  const manifest = await fetchManifest(id);
  if (!manifest) {
    console.debug("[sceneguard] no manifest for", id.key, "- watching unfiltered");
    return;
  }
  const { spans, annotated, muted } = resolveManifest(manifest.segments || [], {
    blocked: prefs.blocked,
    modes: prefs.modes,
    minConfidence: prefs.minConfidence,
    pad: prefs.pad,
  });
  state = { ...state, video, spans, muted, box: state.box || mount(video), annotated };
  console.info(`[sceneguard] ${id.key}: ${spans.length} seek edit(s), ${annotated.length} annotation(s)`);

  let ticking = false;
  const tick = () => {
    if (ticking || video.paused || video.seeking) return;
    ticking = true;
    try {
      const t = video.currentTime;
      const d = decide(t, spans, { preRoll: 0.35, grace: 1.2 });
      const ui = state.box;
      if (d.action === SKIP) {
        video.currentTime = Math.min(d.target, video.duration || d.target);
        state.lastJumpAt = performance.now();
        ui.querySelector("[data-state]").textContent = `skipped: ${d.label || "flagged scene"}`;
        setTimeout(() => ui.querySelector("[data-state]").textContent = "", 3500);
      } else {
        const cd = countdown(t, spans);
        const btn = ui.querySelector("[data-act=skip]");
        if (cd && cd.seconds > 0 && cd.seconds < 30) {
          btn.style.display = "";
          btn.querySelector("[data-why]").textContent = `in ${cd.seconds.toFixed(0)}s`;
          btn.onclick = () => {
            const next = spans.find((s) => s.start >= video.currentTime);
            if (next) video.currentTime = next.target ?? next.end;
          };
        } else if (t < (spans[0]?.start ?? Infinity) || !spans.some((s) => t >= s.start && t < s.end)) {
          btn.style.display = "none";
        }
        // 'ask' entries: pause once and let the viewer decide - the safety valve
        const ask = (state.annotated || []).find((a) => a.mode === "ask" && t >= a.start - 0.2 && t < a.start + 0.4);
        if (ask && !ask.__asked) {
          ask.__asked = true;
          video.pause();
          const go = window.confirm(`SceneGuard: possible ${ask.categories.join("/")} ahead (${Math.round(ask.end - ask.start)}s). Skip it?\nOK = skip, Cancel = watch it.`);
          if (go) video.currentTime = ask.end + 0.2;
          video.play().catch(() => {});
        }
      }
      // language mode mutes audio, never the picture
      const inMute = state.muted.some((m) => t >= m.start && t < m.end);
      if (inMute !== video.muted) video.muted = inMute;
    } finally {
      ticking = false;
    }
  };
  clearInterval(state.timer);
  state.timer = setInterval(tick, TICK_MS);

  addEventListener(
    "keydown",
    (e) => {
      if (e.target?.isContentEditable || /input|textarea/i.test(e.target?.tagName || "")) return;
      if (e.key === ",") {
        state.markStart = video.currentTime;
        state.box.querySelector("[data-state]").textContent = `mark start ${video.currentTime.toFixed(2)}s`;
      } else if (e.key === "." && state.markStart != null) {
        const seg = {
          start: +state.markStart.toFixed(2),
          end: +video.currentTime.toFixed(2),
          categories: (prompt("categories?", "sex") || "sex").split(",").map((x) => x.trim()),
          confidence: 0.6,
          label: "community submission from " + id.host,
          source: "community",
        };
        state.markStart = null;
        fetch(`${(prefs.api).replace(/\/$/, "")}/v1/skips`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ ...id, segments: [seg] }),
        })
          .then((r) => r.json())
          .then((j) => (state.box.querySelector("[data-state]").textContent = `submitted (needs ${j.needed ?? 2} more votes)`))
          .catch(() => (state.box.querySelector("[data-state]").textContent = "submitted for review"));
      }
    },
    true
  );
}

/** players swap the <video> node when you change episode, so poll for it */
let seen = new WeakSet();
const poll = setInterval(async () => {
  const v = document.querySelector("video");
  if (!v || seen.has(v)) return;
  seen.add(v);
  await attach(v);
}, 1000);

chrome.runtime?.onMessage?.addListener((m) => {
  if (m?.type === "prefs-changed") {
    clearInterval(state.timer);
    state = { ...state, box: null };
    seen = new WeakSet();
    clearInterval(poll);
    location.reload();
  }
});

    clearInterval(state.timer);
    state = { ...state, box: null };
    seen = new WeakSet();
    clearInterval(poll);
    location.reload();
  }
});
