---
name: frontend-performance
description: Diagnose and fix real page-speed problems by measuring Core Web Vitals (LCP, INP, CLS) with Lighthouse, the DevTools performance panel, and the web-vitals library, rather than by applying generic advice. Covers image loading and sizing, code splitting, font loading, layout shift, long tasks and yielding, and network/waterfall fixes. Use when a page is "slow", when Lighthouse or PageSpeed flags LCP/CLS/INP, when users report jank or a slow first interaction, or when a bundle is too big. Triggers on "LCP", "INP", "CLS", "Core Web Vitals", "lighthouse", "first load JS", "bundle size", "slow to interactive", "jank", "lazy loading", "code splitting", "shimmer".
compatibility: Browser DevTools, Lighthouse (Chrome), and the `web-vitals` npm library; examples in JS/TS and Next.js where noted.
metadata:
  version: "1.0"
---

# Frontend Performance

Performance work is diagnosis. The same "optimise images" advice is either the
whole fix or irrelevant depending on which metric is bad and which element
caused it. Measure first, name the element, fix that.

## Workflow

Progress:

- [ ] 1. Establish the metric that matters (LCP / INP / CLS) with field data if
      available, lab data otherwise
- [ ] 2. Get the *element* or *script* the metric blames, not just the score
- [ ] 3. Confirm the cause in a DevTools trace (network waterfall, main-thread
      flame chart, layout-shift regions)
- [ ] 4. Apply one targeted fix
- [ ] 5. Re-measure; keep the fix only if the metric moved

## Step 1 — Which metric, and is the score the problem

**Lab (Lighthouse, DevTools) vs field (real users)** disagree often. Lab is a
simulated throttled run on a synthetic device; field is what real users feel.
Optimise the field number if you have it; use lab to find *why* it's bad.

| Metric | What it measures | "Good" target | What a bad number usually means |
|---|---|---|---|
| **LCP** (Largest Contentful Paint) | When the largest visible element finishes painting | ≤ 2.5s (75th pct) | A slow hero image, a render-blocking font, a slow server response |
| **INP** (Interaction to Next Paint) | Latency from a tap/click/key to the next frame that shows the response | ≤ 200ms | A long JS task on the main thread when the user interacts; heavy handlers |
| **CLS** (Cumulative Layout Shift) | How much visible content jumps during load | ≤ 0.1 | Images/ads/iframes without dimensions, web fonts swapping in, content injected above existing |

Report the 75th percentile, not the mean. The mean hides the bad experiences.

## Step 2 — Get the blame, not the score

A Lighthouse score is a summary; you need the artifact.

- **Lighthouse** (Chrome DevTools → Lighthouse, or the CLI):
  ```bash
  npx lighthouse https://example.com \
    --only-categories=performance \
    --output=json --output-path=./lh.json --chrome-flags="--headless"
  # The LCP element and its breakdown are in lcp-lazy-loaded / largest-contentful-paint-element audits.
  ```
  Read the **LCP element** audit — it names the exact node. Read the
  **layout-shift-elements** audit — it names what moved. Read
  **long-tasks** and **bootup-time** / **mainthread-work-breakdown** for INP
  suspects.

- **Field data via `web-vitals`**: ship the real numbers to your analytics, so
  you optimise what users feel, not a lab run.
  ```ts
  import { onLCP, onINP, onCLS } from 'web-vitals/attribution';
  onLCP(({ value, attribution }) =>
    report({ metric: 'LCP', value,
             element: attribution.largestShiftTarget || attribution.element }));
  onINP(({ value, attribution }) =>
    report({ metric: 'INP', value, target: attribution.interactionTarget }));
  onCLS(({ value, attribution }) =>
    report({ metric: 'CLS', value, worst: attribution.largestShiftTarget }));
  ```
  The `attribution` build tells you *which element / which input* caused the
  shift or the INP. Without it you just know the number is bad.

- **DevTools Performance panel**: record a load, look at the **Network** track
  for waterfalls and the **main thread flame chart** for long tasks. For a jank
  complaint, enable **4× CPU throttling** and interact — the flame chart shows
  the long task that blocked the click.

## Step 3 — Fix LCP (the thing people mean by "slow site")

LCP is almost always one of four. Find which:

1. **The server response is slow (TTFB is high).** LCP ≈ TTFB + render. If
   TTFB is > ~800ms, the problem is your server/CDN, not the front end. Fix
   caching, edge rendering, or the database query — not images.
2. **The LCP image is the hero and it's huge or unsized.**
   - Right-size it: serve at the *displayed* size × DPR, not the source size.
     A 4000px hero shipped to a 700px slot is pure waste and decode time.
   - Modern format: AVIF (best) or WebP, with a fallback via `<picture>`.
   - `fetchpriority="high"` on the LCP image; `loading="lazy"` on everything
     *below the fold* (never the LCP image).
   ```html
   <img src="hero.avif" alt="…"
        width="1200" height="630"          <!-- always, see CLS -->
        fetchpriority="high" decoding="async" />
   ```
   In Next: `next/image` with `priority` (LCP) and sensible `sizes`; it emits
   `srcset` and lazy-loads the rest. Set `sizes` correctly or it ships the
   largest candidate to every device.
3. **A render-blocking font.** Fonts block text paint (FOIT) or swap and cause
   CLS. Use `font-display: swap` (or `optional`), preload the one LCP-critical
   font, subset it, and `size-adjust`/fallback-match it.
4. **The main thread is busy parsing/executing JS before it can paint.** Reduce
   the critical path — code-split (below) and defer non-critical JS.

## Step 4 — Fix INP (jank on interaction)

INP is *responsiveness*: when the user taps, how fast does the UI react? The
cause is a **long task on the main thread** during the interaction.

- **Break up long tasks.** Any task >50ms is "long"; INP counts all of them
  during the interaction window. Yield between units of work:
  ```ts
  // Process 1000 items without blocking input for 300ms straight.
  async function processAll(items) {
    for (let i = 0; i < items.length; i += CHUNK) {
      processChunk(items.slice(i, i + CHUNK));
      await new Promise((r) => setTimeout(r, 0));  // yield to input
    }
  }
  // Better: scheduler.yield() where available, or requestIdleCallback for non-urgent work.
  ```
  `await scheduler.yield()` (or `setTimeout(0)`) between chunks, and use
  `requestIdleCallback` for work that does not block the next interaction.
- **Move work off the main thread.** Heavy parsing, image processing, large
  sorts/JSON → a Web Worker. Workers don't block INP.
- **Make event handlers cheap.** A handler that does a full synchronous render
  of a big list, or a big JSON parse, is an INP hit. Defer non-visible work.
- **Virtualise long lists** (see the `react-native-performance` skill for the
  RN analogue; on the web use a windowing lib like `react-window`, or native
  `content-visibility: auto`).
- **Watch the main thread for hydration cost in React.** A large client bundle
  that hydrates on load delays the first interaction being cheap — code-split
  and lazy-load below-the-fold interactive parts.

## Step 5 — Fix CLS (content that jumps)

CLS is movement of *visible* elements during load. Every fix is "reserve the
space":

- **Set `width` and `height` (or `aspect-ratio`) on every image, video,
  iframe, and embed.** The browser then reserves the box before load. An
  unsized image is the single most common CLS source.
  ```html
  <img width="800" height="450" src="…" />        <!-- intrinsic ratio -->
  <div style="aspect-ratio: 16/9"><video …></div> <!-- media without dims -->
  ```
  With dynamic images, Next `Image`, CSS `aspect-ratio`, or a skeleton of the
  exact size.
- **Web fonts swapping in.** `font-display: swap` shows a fallback first, but
  the swap itself shifts lines. Match the fallback metrics with
  `size-adjust`, `ascent-override`, `descent-override` on a `@font-face` fallback,
  or use `font-display: optional` (no swap within the load window). Self-host
  and preload the critical font; don't use three font weights you don't need.
- **Don't insert content above existing content** on load. Ads, banners,
  cookie bars that push everything down, or a "new messages" pill injected at
  the top all score CLS. Reserve their space or animate them in from a
  non-shifting position.
- **Animate transform/opacity, not `top`/`width`/`margin`.** Transforms don't
  trigger layout or shift neighbours; they also run on the compositor.
  ```css
  .drawer { transition: transform .2s; }        /* not: top/height */
  ```
- **Skeleton/placeholder of the right size** for async content, so it fills the
  exact space the content will occupy.

## Step 6 — Code splitting and the bundle

- **Ship JS for what's above the fold and interactive now; defer the rest.**
  - Route-level splitting is automatic with modern routers (`next/dynamic` for
    Next pages, `React.lazy` + `Suspense` in SPAs):
    ```tsx
    const Editor = lazy(() => import('./Editor'));
    <Suspense fallback={<EditorSkeleton />}>
      <Editor />
    </Suspense>;
    ```
  - Next: `next/dynamic` (or route segment `loading.tsx`) for heavy components;
    `ssr: false` for client-only widgets.
- **Watch the actual cost, not the file count.** "First Load JS" per route in
    the Next build output is the number to reduce. Check what's big: a date
    lib, a moment/dayjs, an icon set imported wholesale, a charting lib.
  - Import icons individually (`import Icon from 'lucide-react/icons/x'`), not
    `import * as Icons`.
  - A heavy dependency on a rarely-used path is the classic win; `moment`,
    `lodash` imported as a whole, and full icon/emoji sets are frequent culprits.
- **Defer third-party and non-critical scripts** with `defer`/`async`, or load
  them after interaction/`requestIdleCallback`. Analytics, chat widgets, and ad
  tags in the critical path are pure INP/LCP tax.
- **Preload the LCP image and critical font**, but do not preload everything —
  too many preloads contend for bandwidth and can make things slower.

## Step 7 — Fonts and images in detail

**Fonts**

```css
@font-face {
  font-family: Inter;
  src: url(/fonts/inter-var.woff2) format('woff2');
  font-display: swap;         /* paint fallback immediately, avoid FOIT */
  size-adjust: 100%;          /* tune a fallback's metrics to reduce shift */
}
```

- Preload the one font used in the LCP/above-fold text:
  `<link rel="preload" as="font" type="font/woff2" href="… " crossorigin>`.
  (`crossorigin` is required even same-origin, or the preload is fetched
  twice.)
- Subset to the characters you use; self-host; prefer `woff2`.
- `font-display: optional` is the strongest CLS fix (no swap mid-load) at the
  cost of sometimes not showing the web font on a cold load — a deliberate
  trade.

**Images**

- Right size (`sizes`/`srcset` for DPR), right format (AVIF > WebP > JPEG), and
  always dimensions (CLS).
- Lazy-load below the fold (`loading="lazy"`), eager + `fetchpriority="high"`
  for the LCP image. Never lazy-load the LCP image — it delays LCP.
- Serve responsive candidates; a single 2000px image to a phone wastes
  bandwidth and decode.
- Decode cost matters: huge images block paint. Modern formats and right-sizing
  cut it.

## Gotchas

- **`preload` the LCP image or fetchpriority="high", not both from different
  URLs** — mismatched URLs cause a double download. One mechanism, one URL.
- **A placeholder image that later loads a bigger one causes CLS and double
  download.** Serve the real (right-sized) image, not a tiny placeholder.
- **Lazy-loading too much hurts LCP**, because lazy images below/near the fold
  start loading late. Lazy-load genuinely below-the-fold, not the first screen.
- **A spinner that replaces content changes size** → CLS. Size it to the final
  layout or reserve the space.
- **CLS ignores shifts within 500ms of user input**, so a deliberate
  accordion expand on click is free; a load-time shift is not.
- **INP replaced FID** as a Core Web Vital. Long tasks are the enemy of both.
- **Lab INP from Lighthouse is not field INP**; Lighthouse's "TBT" (Total
  Blocking Time) is a proxy for main-thread jank, not the metric itself.
- **`content-visibility: auto` + `contain-intrinsic-size`** massively cuts
  initial render work for long pages in Chromium; give a sensible
  `contain-intrinsic-size` or you'll trade CLS for scrollbar jumps.
- **Hydration of a huge client tree is a long task at load** and delays the
  first cheap interaction; it shows up as TBT/INP pressure even before any
  click.
- **Don't `await` a chain of independent fetches** — waterfalls are a common
  TTFB/LCP cause. `Promise.all` independent ones.
- **A 3rd-party script that blocks the main thread hurts every metric** and you
  cannot fix its code; you can defer or remove it.
- **Throttling matters for realistic testing**: DevTools "Slow 4G" + "4× CPU"
  throttle approximates a mid-range phone. Optimising on a fast dev machine
  hides the problem.

## A note on scoring

Chasing a Lighthouse number on a laptop is a trap. Set a field-SLO
("75th-percentile LCP ≤ 2.5s on real users"), and use the lab only to find the
cause. If field data shows a good median but a bad p75, the problem is a subset
of users on slow devices/networks — profile for that subset, not the average.

## Files

- `references/cwv-diagnosis.md` — metric-by-metric diagnosis flows, and a
  symptom → likely cause → tool-to-confirm table.
