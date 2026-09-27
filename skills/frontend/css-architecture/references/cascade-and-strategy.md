# Cascade and strategy reference

Load this when you need to settle a specific cascade fight, or when choosing
between CSS Modules, a utility framework, and layers for a codebase.

## Specificity, fully

The tuple is `(inline, ids, classes-level, types-level)`, compared
component-wise, left to right.

- **inline** = a `style="…"` attribute. Any inline style beats any selector
  (unless the selector has `!important`, which beats inline).
- **ids** = count of `#id`. Any single ID beats any number of classes.
- **classes-level** = class selectors `.x`, attribute selectors `[x]`, and
  pseudo-classes `:hover`, `:focus`, `:nth-child()`, plus whatever
  `:is()`/`:not()`/`:has()` bring in.
- **types-level** = element selectors `div`, and pseudo-elements `::before`.

### What each selector contributes

| Selector | Tuple |
|---|---|
| `*` | (0,0,0,0) |
| `p` | (0,0,0,1) |
| `ul li` | (0,0,0,2) |
| `.nav` | (0,0,1,0) |
| `.nav .item.active` | (0,0,2,0) |
| `.nav .item:not(.disabled)` | (0,0,2,0) (`:not()` = its argument) |
| `.nav .item:is(.a, .b)` | (0,0,2,0) (`:is()` = most specific arg) |
| `.nav :where(#x, .y)` | (0,0,1,0) (`:where()` = 0) |
| `#app .nav` | (0,1,1,0) |
| `.a#app .nav` | (0,1,1,0) (a class + id + class: still one id) |
| `div#app .nav > li` | (0,1,1,2) |
| `li:nth-child(2n+1)` | (0,0,1,1) |

### Common comparisons

- `.a.b.c` (0,0,3,0) vs `#x .y` (0,1,1,0) → **id wins** (0,1,…) > (0,0,…)
  even though 3 > 1. This is why "I added more classes and it still didn't
  work."
- `.card.selected` (0,0,2,0) vs `.card` (0,0,1,0) → selected wins.
- `.card:where(.selected)` (0,0,1,0) vs `.card` (0,0,1,0) → **equal**, so
  source order decides. This is how you make a modifier *not* auto-win.
- `.a` (0,0,1,0) vs `.a` (0,0,1,0) in two files → source order (import order)
  decides. Non-determinism is why layers/modules exist.

## The layer-override recipe

Declare priority once, at one place, then forget specificity:

```css
/* layers.css — imported first, globally */
@layer reset, vendor, tokens, base, layout, components, utilities, overrides;
```

```css
/* anything third-party */
@import url(framework.css) layer(vendor);

/* your code */
@layer components {
  .card { border: 1px solid; }
  .card--flat { border: 0; }
}
@layer utilities {
  .p-0 { padding: 0 !important; }   /* utilities may use !important */
}
@layer overrides {
  .legacy-widget .card { border: 2px dashed; }  /* wins over components */
}
```

Within a layer, specificity still decides; across layers, **the later layer
wins regardless of specificity**. The `utilities` layer beating `components`
lets a single-class utility reliably beat a multi-class component without
`!important` in either.

Gotcha: **unlayered CSS beats all layers.** If a global/reset file or an
injected vendor stylesheet is unlayered, your highest layer still loses. Put
resets in a layer too, and layer third-party CSS with `@import … layer()` or a
wrapper.

## Strategy decision

| Situation | Use | Why |
|---|---|---|
| A component library, React/Vue, many components | CSS Modules | Automatic scoping, no runtime, works with any CSS features |
| A design system with a token scale and theming | Layers + custom properties (+ Modules) | Theming is data; layers stop the war |
| A fast-moving product team, consistent spacing, want dead-CSS removal | Tailwind | Scale enforced by the config; purge removes unused |
| A marketing/content site, small team | BEM + a reset + a few layers | Low ceremony, readable class names |
| You already use Sass/CSS-in-JS | Keep the nesting, add layers | Don't migrate; just add `@layer` and tokens |

**Do not run two primary systems.** A codebase half-Tailwind, half-BEM has
neither the utility scale nor the BEM predictability.

## Nesting vs flat

Native CSS nesting (`&`) expands to the parent selector, so:

```css
.card {
  color: black;
  & .title { font-weight: bold; }     /* .card .title  -> (0,0,2,0) */
  &:hover { color: blue; }
}
```

Nesting keeps specificity tied to the nesting depth, which can make overrides
harder to reason about. It's fine for building a component's own internals, but
for "a variant another team must override", keep the external surface flat and
low-specificity:

```css
/* Expose a predictable, overridable hook: */
.card :where(.title) { … }   /* (0,0,1,0) regardless of .title's own rules */
```

## Stacking contexts and z-index

`z-index` is only compared **within a stacking context**. A stacking context is
created by:

- root element
- `position: fixed` / `sticky` (usually)
- `position` other than static/relative **with** `z-index` other than auto
- `opacity < 1`
- `transform`, `filter`, `backdrop-filter`, `perspective`, `will-change:
  transform/filter`, `contain: paint/layout`
- `mix-blend-mode` other than normal

So `z-index: 9999` on a child of a `transform`ed ancestor is trapped inside
that ancestor's context. Recipe: normalise with tokens, avoid
`will-change: transform` unless you need it, and if a fixed/modal element
misbehaves, look for an ancestor with `transform`/`filter`/`overflow:hidden`
creating a context.

```css
:root {
  --z-base: 0; --z-dropdown: 10; --z-sticky: 20;
  --z-overlay: 100; --z-modal: 200; --z-toast: 300;
}
```

## Theming via tokens

```css
:root { color-scheme: light dark; --bg: #fff; --fg: #111; }
@media (prefers-color-scheme: dark) { :root { --bg: #111; --fg: #eee; } }
[data-theme="dark"] { --bg: #111; --fg: #eee; }  /* explicit override wins */
```

Components use `var(--bg)`; a theme flip is one block of token assignments, no
component selector changes, and dark mode never causes a specificity fight.
