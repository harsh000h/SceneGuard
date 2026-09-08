# Android prototype: architecture, model, data, and how to test it without touching DRM

Answers the offer in your last message, and starts by fixing two assumptions in it
that would have cost you a month.

---

## 0. Two corrections first

**Correction 1 — "Modern computer-vision models can do that reasonably well. The
difficult part is how do you control playback."** The second half is right. The first
half is wrong in a way that changes the roadmap: on Android you cannot get the frames
*at all*, for any stream, so there is no "real-time AI detection on OTT content"
version to build later either.

Two independent OS-level blocks, not one:

| Block | What it stops |
|---|---|
| Widevine L1 secure path — decrypted frames go from the DRM HAL to the video decoder over a secure input buffer, never into app-readable memory | `MediaCodec`/`ImageReader` access to frames of protected playback |
| `FLAG_SECURE` — window content is excluded from screenshots, `MediaProjection` recording **and casting to non-secure displays**; still enforced on Android 14/15 | the "just screen-record and look at pixels" fallback |

So your Version 3 ("real-time universal filter") is not hard — it is architecturally
out. Reframe the three versions:

```
V1 timestamps DB   ->  the SHIPPED ARTIFACT. tiny, precise, legal.
V2 AI detection    ->  the FACTORY. runs once per title, offline, on content we may
                       lawfully decode, output = drafts of V1 rows for a human to confirm.
V3 real-time       ->  does not exist. Anyone who ships this is circumventing and
                       will be VidAngel'd. Delete it from the roadmap.
```

The reason V2 belongs in the factory and not the player: your viewer seeks. A
real-time detector at 01:23:14 has to be at 01:23:14, and it cannot be — so you would
re-scan a 2-hour film for every viewer, for a result identical every time. Compute
once, distribute data. This is also why VidAngel and ClearPlay are both companies
that mostly sell *data* through a thin client.

**Correction 2 — "don't promise it works with every OTT platform, not initially."**
Agree, but the reason is not engineering risk, it's data volume, and it's quantifiable:

```
5 platforms x ~3,000 Indian-relevant titles = 15,000 titles
~1.5 min of human confirm per title (drafted by V2)  =  375 hours
one person, full time                                =  ~9 weeks of confirmations
```
That is the entire real cost of "universal", and it's why Skipit is Netflix-only.
Budget for the labelling, not for the model.

---

## 1. The control surfaces, ranked by legality × feasibility

| Surface | Control mechanism | Legal posture | Verdict |
|---|---|---|---|
| Desktop/laptop Chrome (Netflix, Prime, JioHotstar, Sony LIV, Zee5 all use an HTML5 `<video>`) | `video.currentTime`, `video.muted` | Seek-past on an authorised stream; US analogue is the Family Movie Act; **India has no equivalent**, so stay strictly in "no copy, no decrypt" | **Build this.** Already working in this repo |
| **Android TV / Fire TV box: our own app hosting a WebView of the platform's web player** | same `video.currentTime` inside our WebView | We control the container, not the stream. No `FLAG_SECURE` window, no capture, no DRM touch | **The actual living-room product.** See §2 |
| Android phone app injecting into the real Netflix APK | none available | requires hooking another process | No |
| Android `MediaSession` remote control | `MediaSessionManager.getActiveSessions()` requires a `NotificationListenerService`; `MediaController.TransportControls.seekTo()` is only as good as the target app implementing it (Netflix does not reliably expose seek) | lawful but useless | Fallback remote only |
| AccessibilityService overlay that taps a web player's timeline | pixel tapping | works; Play policy + fragility make it a treadmill | Avoid |
| Local files (user-owned, DRM-free) in our own `Media3` player | full control | cleanest possible: the user's own file, permission granted | **Build this too** — it's the V2 factory *and* the offline player in one app |

The Android-TV-WebView row is the one to internalise: it turns "we cannot touch the
TV" (your message, and my earlier note about ClearPlay refusing TV apps) into
**"we cannot touch *their* TV app — so we become the browser the TV already allows."**
It also fits the Indian install base far better than a laptop:
a ₹2,500 Fire TV Stick and a phone as remote beats "carry the laptop downstairs".

---

## 2. System architecture (v0.1 — two components, one data format)

```
        ┌─────────────────────────── cloud ────────────────────────────┐
        │  /v1/manifest?platform&title&season&episode  -> manifest JSON │
        │  /v1/skips  POST (community submissions, dedupe, votes)       │
        │  curation UI + scan queue  (drafts from V2, human confirm)    │
        └───────────────┬───────────────────────────────┬──────────────┘
                        │ HTTPS, ETag, offline cache     │
   ┌────────────────────▼─────────────────┐  ┌──────────▼───────────────────────┐
   │ A. SceneGuard Local (Android app)     │  │ B. SceneGuard Box (Android TV)    │
   │  · Media3/ExoPlayer for own files     │  │  · WebView → platform web player  │
   │  · on-device SCAN (Chaquopy: reuse    │  │  · same core.mjs as desktop ext   │
   │    sceneguard/*.py — already tested)  │  │  · `video.currentTime` + muted    │
   │  · skip + mute + mark lanes           │  │  · pairing = 6-digit code, LAN     │
   │  · writes confirmed manifests up      │  │  · phone = remote + skip log      │
   └───────────────────────────────────────┘  └───────────────────────────────────┘
```

Shared pieces: `sceneguard/schema.py` (manifest contract), `extension/core.mjs`
(decision function, 12 checks green), `sceneguard/skipcore.py` (merge / snap /
resync, 19 checks green). Port them, don't reimplement — the bugs the tests
already caught are the ones you'd otherwise rediscover in week six.

```kotlin
// A: Media3 lane — no reflection, no capture, no hooking. ~40 lines of runtime.
player.addListener(object : Player.Listener {
    override fun onEvents(p: Player, e: Player.Events) {
        if (!p.isPlaying) return
        val t = p.currentPosition / 1000.0
        when (val d = decide(t, spans, 0.35, 1.2)) {          // core port, tested
            SKIP -> { p.seekTo((d.target ?: d.end).toLong() * 1000); ui.flash(d.label) }
            else -> p.volume = if (inMute(t, muted)) 0f else 1f // mute never removes picture
        }
    }
})
```

---

## 3. Detection model (V2 = factory), and what to refuse

Do not train a from-scratch classifier. Per-title pipeline:

1. **Shot segmentation** — adaptive boundary map (median + 3·MAD). Fixed thresholds
   are the mistake: the same constant that finds nothing in a chamber drama finds
   400 "scenes" in an action film. Already in `vision.py`; cut from 17 → 8 phantom
   edges on our noisy synthetic by collapsing bursts.
2. **Frame-level NSFW score per shot** — run an existing open classifier per keyframe,
   average over the shot, require ≥ 1.5 s to count. A shower scene in a thriller and
   a shower scene in a romance score identically at pixel level — which is exactly why
   step 3 is mandatory, not optional.
3. **Caption corroboration lane** — score cues against the unencrypted VTT/TTML track.
   This is the lane that fixes context, and it is the one that makes Indian-language
   coverage cheap: dialogue around these scenes is expository.
4. **Two-list projection** — strong + skip-capable → `blocking`; everything else →
   `annotations` (mark / mute / ask). Never pad-before-merge, never union categories
   across unrelated content. Both rules exist because a test caught them (README §3).
5. **Human confirm** in the curation UI — model output is a *draft row*, never live data.

Evaluation, before any of this is trusted:
- build a 200-title labelled set from **public-domain / CC films + your own family
  files**, stratified: skin-tone range, warm Indian interior lighting, bed linen vs
  skin (the top false-positive class), non-western framings.
- ship no auto-skip below **90 % precision** on *your* set. Public benchmark numbers
  are meaningless here — they're measured on datasets that look nothing like a Marathi
  or Bhojpuri film. Recall matters less: a missed scene costs one scene; a false skip
  costs the plot, and the plot is why your parents tolerate the movie at all.

---

## 4. Database (5 tables; the interesting part is the trust model, not the schema)

```sql
platform(id, name, base_url, id_kind)                      -- netflix|prime|hotstar|local
title(id, platform_id, ext_id, tmdb_id, title, season, episode,
      duration_s, boundary_map JSON, updated_at)            -- boundary_map = resync key
segment(id, title_id, start_s, end_s, categories JSON,      -- {category: action}
        confidence REAL, state TEXT, reviewed_by, reviewed_at,
        support_count INT)                                   -- pending|verified|disputed
submission(id, title_id, start_s, end_s, contributor, ts, score REAL)
contributor(id, trust REAL, accepted INT, reverted INT, last_active_at)
```

Verification rule (the thing that makes crowd data usable): one submission =
`pending`; ≥2 independent submissions overlapping at **IoU ≥ 0.3** = `verified`,
averaged with weights `0.6·trust + 0.4·recency`; `disputed` if a verified span is
downvoted by two contributors above 0.7 trust. Every edit is a `submission` row, so
an incorrect skip is revertible and traceable — which is also your answer when a
parent asks "why did it cut that part out".

`segment.categories` stores *per-category actions* because two family members want
different things from one title, and a `segment` that encodes only "adult yes/no"
forces the all-or-nothing product Netflix already has.

---

## 5. Testing without touching DRM

| Harness | What it proves | Where |
|---|---|---|
| `python -m sceneguard selftest` | detector → manifest → skip decisions, incl. zero-frame-leak at 30 fps, violence kept, weak evidence degrades | **19/19 passing now** |
| `node extension/test-core.mjs` | the shipping JS core against the *real* generated manifest | **12/12 passing now** |
| Public-domain / CC features (Internet Archive) | model + resync on real cinema, legally, at scale — build the labelled eval set here | TODO, unblocks everything |
| Your family's own downloaded files | the Android app's real workload: local scan → confirm → play with skips | manual, per device |
| Web-player seek check | that `video.currentTime` still works after each platform UI change — a Playwright smoke test on the *web* player, our own session | TODO, CI |
| Never | decrypting, capturing, re-encoding, re-hosting a stream | — |

Hard acceptance line for v0.1: 0 blocked frames painted, 0 skips outside a verified
`blocking` span, and a manifest whose coverage number is displayed in the UI. If a
title has no data, the app says "no data" — never "safe". (That failure mode is why
`sceneguard/rating.py` refuses to render `none` as `safe` without evidence.)

---

## 6. Build order

1. **Week 1 — prove the family uses it.** Chrome extension (already here) + one JSON
   file of 30 hand-curated Indian titles. Laptop + HDMI to the TV. Nothing else. If
   they don't use it for three weeks, stop: you've spent a week, not a quarter.
2. **Week 2–3 — Android app A** (Media3 + local scan via Chaquopy + import manifests).
   This is where V2 gets real mileage, on files we're allowed to decode.
3. **Week 3–4 — Box build B.** Android TV WebView wrapper. This is the living-room
   wedge and the differentiator nobody in this market has: VidAngel/ClearPlay/Skipit
   are all laptop-and-TV-apps, all US-catalogue, all English-first.
4. **Then** the phone as remote + skip log (parents shouldn't need a keyboard), then
   the curation UI, then `/v1` with ETag + offline cache.
5. Before taking money: one media-law read on (a) community timestamp storage,
   (b) hosting manifests for titles we never copy, (c) India having **no Family Movie
   Act** — §52's "fair dealing … private or personal use" covers reproduction, not
   making an adapted version. §1201's US safe-ish harbour does not exist here; our
   exposure is ToS (account termination) and *not* circumvention, as long as we stay
   in seek-and-mute. Budget ₹30–50k, before launch, not after the first email.

## 7. Naming warning

Do not market it as "filters Netflix". Market it as **family-safe viewing and
trigger control** — the same mechanism covers autism (sensory triggers), epilepsy
(flash counts — the boundary map is already computed), PTSD and gore sensitivity.
That framing is technically identical, ~10× the audience, and it is the difference
between "a tool families depend on" and "a tool a studio sends a lawyer about".
