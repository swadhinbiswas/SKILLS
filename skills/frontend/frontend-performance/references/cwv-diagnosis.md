# Core Web Vitals diagnosis reference

Load this when a specific metric is bad and you need to go from "CLS is 0.4" to
the element and the fix. The main `SKILL.md` has the fixes; this has the
diagnosis paths and the confirming tool for each cause.

## Metric → cause → confirming tool

### LCP (Largest Contentful Paint)

LCP is the paint time of the largest image/text/block in the viewport. The
breakdown in DevTools/Lighthouse is: **TTFB → resource load delay → resource
load duration → element render delay**. Walk it in that order — each phase
points at a different fix.

| Phase that's long | Cause | Confirm with | Fix |
|---|---|---|---|
| TTFB | Slow server / DB / cold cache | Network panel, `curl -w '%{time_starttransfer}'` | Cache, edge/CDN, fix the query, SSR caching |
| Resource load delay | LCP image not discovered / prioritised / late | Network panel priority column, "LCP request discoverable" audit | `fetchpriority="high"`, preload, discoverable in initial HTML (not JS-injected) |
| Resource load duration | Oversized, unoptimised, or wrong-format image or font | Lighthouse "uses-optimized-images", "uses-responsive-images" | Resize, convert to AVIF/WebP, subset font |
| Element render delay | Main thread busy; render-blocking CSS; font FOIT | Main-thread flame chart, "render-blocking-resources" audit | Code-split, defer CSS, `font-display` |

LCP is **only** measured until the first user interaction; anything after that
scroll is not LCP. Also: the largest element is recomputed as content loads, so
a late-loading hero can *become* the LCP element.

### CLS (Cumulative Layout Shift)

CLS sums layout-shift scores for unexpected shifts of visible elements during
the page's life (excluding shifts within 500ms of input). Use Lighthouse's
**"layout-shift-elements"** audit (names the shifting nodes and the score) and
`web-vitals/attribution`'s `largestShiftTarget`.

| Shifting element | Cause | Fix |
|---|---|---|
| `<img>` without dimensions | Image loads and pushes content | `width`/`height` or `aspect-ratio` |
| Video/embed/iframe without size | Same | Reserve via `aspect-ratio` or a sized wrapper |
| Text block after web font | Font swap changes line height | `size-adjust`/metric-matched fallback, `font-display: optional`, preloaded font |
| Banner/announcement injected at top | Pushes everything down | Reserve space or position non-shifting (`position: absolute` until placed) |
| Ad slot without reserved size | Fills late, pushes content | Pre-size the slot to the ad's known height |
| Cookie/consent bar at top | Appears after load | Accept it's CLS; mitigate by appearing in a flow that's expected, or reserve |
| Skeleton of wrong size | Real content ≠ skeleton box | Make the skeleton's box match the final content's box |

Layout shift from a *deliberate* action (accordion on click) is excluded if
within 500ms of input — so don't contort an interaction to "avoid CLS"; that
metric doesn't apply to intentional, input-adjacent changes.

### INP (Interaction to Next Paint)

INP is the worst (98th-percentile-ish) interaction latency during the page's
lifetime. The cause is always a **long task on the main thread that runs between
the input and the next paint**. Diagnose by:

1. Enable **Performance** panel, record, then perform the slow interaction
   *while recording* (DevTools does not throttle on its own unless you set CPU
   throttling).
2. In the flame chart, look for a long task (a wide bar) that starts at the
   input. That bar is the handler + any synchronous re-render.
3. The **Interactions** track and the "Event Timing"/"Long Animation Frame"
  (LoAF) entry point at the interaction pinpoint it. `PerformanceObserver` for
  `long-animation-frame` gives you `scripts` attribution directly.

| Long task source | Fix |
|---|---|
| Big synchronous `render`/reconciliation on interaction | Split the update; defer non-visible parts; virtualise the list |
| Heavy `JSON.parse`/sort/filter in the handler | Move to a worker, or precompute/index, or `await` a yield |
| A large bundle hydrating / a big component remounting on the click | Lazy-load, keep the clicked subtree small |
| Third-party script (analytics/chat) hijacking the main thread | Defer it, load after interaction, or drop it |
| A long `useEffect` chain after a state change on click | Do the work in the event, not cascading effects |

The **first** interaction after load is often the worst (hydration still
running). Budget for that: keep the initial bundle and hydration small.

## Cross-cutting: the loading waterfall

A slow LCP is frequently a chain, not one slow request. In the Network panel,
draw the waterfall for the critical path:

- HTML → (discover) → hero image CSS/fonts → hero image → paint. Each hop adds
  a round trip. Preload the hero image and the critical font to break the
  CSS/font-gated discovery; inline critical CSS to remove the CSS round trip.
- **JS-discovered resources are late.** An image whose `src` is set by React
  after hydration cannot start loading until JS runs. For the LCP image, put it
  in the server HTML (real `<img>`, `fetchpriority`), not behind a client
  effect.
- **Fonts:** CSS → font file. Preload the font file directly to skip the CSS
  hop.

## Measuring in the field (RUM)

Lab is a hypothesis; RUM is the truth. Ship `web-vitals` to your analytics
with attribution and slice by:

- **Device class / CPU** (a bad p75 often = low-end Android).
- **Network effective type.**
- **Route** — a specific route's LCP is bad because of that route's hero.
- **Release** — did a deploy make it worse?

RUM p75 is the number Google uses for CWV ranking; track p75, not mean, and
track it per real-user segment. Alert on a p75 regression after a deploy.

## Quick wins, ordered by typical payoff

1. Right-size and modern-format the LCP image; `fetchpriority="high"`;
   dimensions set.
2. Code-split the route; lazy-load below-the-fold interactive widgets; defer
   third-party scripts.
3. `font-display: swap` (+ `optional` if CLS is the issue) and preload one
   subset font.
4. Reserve space for every image/embed/banner (kill CLS).
5. Break long tasks on interaction (yield / workers) to fix INP.
6. Cut the main-thread work at load: `content-visibility`, virtualise long
   lists, trim the bundle.

Always re-measure after each; keep the ones that move the field metric.
