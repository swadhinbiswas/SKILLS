---
name: css-architecture
description: Keep CSS maintainable in a growing codebase - the cascade and specificity model, cascade layers to end specificity wars, custom properties for theming, container queries and :has with honest support notes, and organising CSS (BEM vs utility tradeoffs) so nobody resorts to !important. Includes a specificity arithmetic refresher. Use when a style "won't apply", when styles fight each other, when a component's styles leak or are hard to override, when a codebase is drowning in !important or one-off overrides, or when choosing a CSS strategy (CSS Modules, Tailwind, layers). Triggers on "z-index not working", "specificity", "!important", "style not applying", "CSS is a mess", "cascade", "BEM", "Tailwind vs CSS Modules", "dark mode theming", "container query", ":has()".
compatibility: Cascade layers, custom properties, container queries and :has() need modern evergreen browsers (see support notes per feature); everything else is universal.
metadata:
  version: "1.0"
---

# CSS Architecture

CSS fails at scale in one specific way: two rules target the same element and
the loser is decided by something invisible — order, specificity, a layer, an
inline `!important`. The fix is not more discipline; it is a system that makes
the winner predictable.

## The model you must internalise

For a given element+property, the winner is decided by, in order:

1. **Origin and importance** — user-agent vs author vs user; and `!important`
   inverts origin order (user `!important` > author `!important` > author
   normal > user normal > UA normal).
2. **`@layer` order** (if the property is set in a layer) — later layers win
   over earlier ones, and an unlayered rule beats *all* layered rules.
3. **Specificity** — (a, b, c) of the matched selectors.
4. **Source order** — later wins.

The two facts people get wrong:

- **Specificity is not a number you can eyeball correctly.** It is a tuple
  compared component-wise: `a` (inline style), then `b` (#id count), then `c`
  (class/. :is()/:where()/pseudo-class/attribute count), then `d` (element/type
  and pseudo-element count). Compare left to right; the first differing
  component decides.
- **`:where()` has zero specificity. `:is()` takes the specificity of its most
  specific argument.** `:is(.a, #b)` is `#b` specificity. `:where(.a, #b)` is
  `(0,0,0)`. This is the single most useful tool for writing overridable
  component styles.

### Specificity arithmetic refresher

Score each selector as `(a, b, c, d)` and compare as a 4-digit tuple.

| Selector | Specificity | Note |
|---|---|---|
| `*` | (0,0,0,0) | matches everything, wins nothing |
| `div` | (0,0,0,1) | one type selector |
| `.btn`, `[type=button]`, `:hover` | (0,0,1,0) | one class / attribute / pseudo-class |
| `div.btn.primary` | (0,0,1,1) | class beats type |
| `#app .btn` | (0,1,1,0) | any ID outranks any number of classes |
| `a#app .btn.primary` | (0,1,1,1) | compare `a` then `b`: this beats `#app .btn` |
| `.btn:focus-visible` | (0,0,2,0) | two class-level selectors |
| `.card:has(> img)` | (0,0,2,0) | `:has()` takes its most specific argument |
| `li:not(.x) .a` | (0,0,2,0) | `:not()` takes its argument's specificity |
| `:is(.a, .b)` | (0,0,1,0) | most specific argument |
| `:where(.a, #b)` | (0,0,0,0) | always zero |
| `style="color:red"` | (1,0,0,0) | inline style, beats all non-`!important` selectors |
| `#a` + `!important` | beats any normal selector | important beats non-important, then re-compare |

Worked comparisons:

- `.a .b .c` (0,0,3,0) **beats** `.a` (0,0,1,0) — more classes.
- `#nav .item` (0,1,1,0) **beats** `.layout .sidebar .item` (0,0,3,0) — the
  ID wins before the class count matters. This is why one stray `#id` in a
  utility is catastrophic.
- `.a:is(.b, .c)` (0,0,2,0) **beats** `.a .b`? No — `.a .b` is (0,0,2,0),
  equal, so source order decides.
- `:where(#x) .btn` (0,0,1,0) **beats** `.a .b .c` (0,0,3,0)? No — 1 < 3, so
  the three classes win. `:where()` removes the ID's power; use it to make a
  base style easy to override.

The practical consequence: **every selector you add raises the floor for
everyone who overrides it.** Prefer one class over nested specificity, and make
"the thing that must be overridable" `:where()`-wrapped or a layer.

## Layers: the real fix for specificity wars

Cascade layers let you declare *priority order* once and then stop caring about
specificity within a layer.

```css
@layer reset, tokens, base, layout, components, utilities, overrides;
```

Declaration order is the priority order: `overrides` beats `utilities` beats
`components`, **regardless of specificity**. Inside a layer, normal specificity
still applies — layers just set the outer ordering.

```css
@layer components {
  .btn { background: blue; }
  .btn.btn-primary { background: purple; }  /* higher specificity, still same layer */
}
@layer utilities {
  .bg-red { background: red; }  /* utilities layer beats components even though
                                   this is (0,0,1,0) vs (0,0,1,0) */
}
```

- **Unlayered styles beat all layered styles.** This is a footgun in reverse:
  if your framework/global CSS is unlayered, your layered overrides never apply.
  Put *everything* in layers, including resets.
- **The order declaration can be an `@import` chain or a single top-of-file
  statement.** If your layers are declared in one place and imported in that
  order, individual files don't need to know the global order.
- **Libraries can be slotted**: wrap Tailwind, a UI kit, or a vendor CSS in a
  low layer (`@layer vendor;`) so your own layers always beat it — this
  replaces most `!important` in overrides of third-party CSS.
- **`:layer()` in `import`**: `@import url("x.css") layer(vendor);` puts the
  whole file in one layer. Modern and very clean.

## The strategy choice: organise, then commit

Pick **one** primary system. Mixing two is what produces the mess.

| Approach | Good for | Cost | Watch out |
|---|---|---|---|
| **CSS Modules** (`*.module.css`) | Component-scoped styles in a component codebase; no runtime | Need a bundler | Global styles, theming and tokens still need custom properties; class names in the DOM are hashed (don't select on them from JS) |
| **Utility-first (Tailwind)** | Consistent spacing/type scale, dead CSS elimination, fast iteration | Markup verbosity, learning curve, dynamic class strings don't work | Arbitrary values creep in; design-system discipline is required or it's noise |
| **BEM + global CSS** | Small teams, marketing pages, a designer-authored system | Manual discipline, no scoping, cascade grows | Needs layers/linting to stay sane; naming drift |
| **Vanilla layers + custom properties** | Design systems, theming, fine control | You build the conventions | Needs explicit architecture; the payoff is total control and near-zero specificity |

**House default:** component styles in **CSS Modules**, design decisions in
**custom properties** on `:root`/container, global resets and utilities in
**cascade layers**, and no `!important` outside the utilities/overrides layer.
Utility frameworks are fine if the codebase already uses one — do not migrate
mid-project to "be consistent".

## Naming and organisation

- **BEM** (`.block__element--modifier`) is a naming convention, not a scoping
  mechanism; it only avoids collisions if the *block* name is unique. Pair it
  with layers or modules if you use it. It reads well in dev tools.
- **Container-scoped component roots**: give a component a root class and
  prefix its internals (`.card__title`), so you can reason about it in
  isolation.
- **Tokenise everything** into custom properties so theming is data, not
  overrides:
  ```css
  :root {
    --color-bg: #fff; --color-text: #111;
    --space-2: 0.5rem; --radius: 8px;
  }
  [data-theme="dark"] {
    --color-bg: #111; --color-text: #eee;   /* only the tokens change */
  }
  .card { background: var(--color-bg); color: var(--color-text);
          padding: var(--space-2); border-radius: var(--radius); }
  ```
  This is the cleanest theming system: media queries or `data-theme` flip
  tokens, components never know which theme.
- **Keep a token scale** (space-1..N, colour ramp) and forbid raw values in
  components. A `2rem` in a component is a design-system leak.

## Modern CSS and honest support

All of these are Baseline in current evergreen Chrome, Firefox, and Safari
(verify against your browser-support policy — caniuse.com or MDN). Node/older
embedded webviews (older Android WebView) lag, so if you support them, provide a
fallback.

- **Custom properties** — universal in evergreen; supported broadly. The
  workhorse of theming. Caveat: they are resolved at computed-value time, so a
  custom property set on a parent inherits into children; setting it on
  `:root` and overriding per theme is the pattern.
- **Cascade layers** (`@layer`, `@import … layer()`) — Baseline
  (2022+). Older browsers ignore `@layer` and fall back to source order, so
  your unlayered fallback order must also be sane. Safe if you declare layer
  order to match the intended cascade.
- **Container queries** (`@container`, `container-type`, `container-name`) —
  let a component respond to *its own* container's size, not the viewport. This
  is the right tool for reusable cards/widgets that go in sidebars and
  full-width. Support: Baseline (2023+). Fallback: media queries, or a JS
  `ResizeObserver` if you must.
  ```css
  .card-host { container-type: inline-size; container-name: card; }
  @container card (min-width: 30rem) {
    .card { display: grid; grid-template-columns: 8rem 1fr; }
  }
  ```
- **`:has()`** — style a parent based on its children. Support: Baseline
  (2023+). It takes the specificity of its most specific argument, so wrap in
  `:where()` if you want it overridable. Great for form state and "card with
  image" without extra classes:
  ```css
  .field:has(:user-invalid) { border-color: red; }
  .card:has(> img) { --ratio: 16/9; }
  ```
  Fallback: add the class from JS, or use the `:invalid`/`:checked` sibling
  selectors that `:has()` replaced.
- **`:is()`, `:where()`** — broadly supported; use them constantly (see
  specificity above).
- **`content-visibility: auto`** — Chromium (and Safari 18+); big win for long
  pages, but pair with `contain-intrinsic-size` to avoid scrollbar jump. If you
  need Firefox today, it's progressive enhancement only.
- **Nesting** (native CSS nesting `&`) — Baseline 2023+; works in modern
  browsers. Nesting *adds* specificity like the selector it expands from
  (`&` is `&`), so it can surprise in overrides — flat + layers is often
  clearer. Verify support for your target.

## Killing `!important` and specificity wars

`!important` is a legitimate tool in exactly two places: **utilities** (a
`.hidden { display:none !important }` that must beat component styles) and
**third-party overrides you don't control**. Anywhere else it is a bug.

To remove it:

1. **The loser is winning because it's more specific.** Lower the winner's
   specificity with `:where()`: `div.card.selected` → `.card:where(.selected)`
   drops to (0,0,1,0).
2. **Or raise the intended winner via a layer**: move `overrides`/`utilities`
   above `components`.
3. **Or restructure** so the override is a token change, not a selector fight:
   set a custom property instead of a hard property.
4. **If two components fight over the same element**, the real problem is
   composition — make one a child with its own class, not a selector aimed at
   the other's internals (`.other .my-class`).

Enforce with tooling: stylelint rules like `declaration-no-important` (outside
allowed layers), `selector-max-specificity`, and a `no-descending-specificity`
check. A specificity budget in CI beats a code-review habit.

## Gotchas

- **Source order beats equal specificity across files**, and import order is
  not obvious. This is the #1 source of "it works on my machine" — the
  specificity is equal, the load order differs. Layers and CSS Modules remove
  the ambiguity.
- **`!important` inverts origin order too**, so author `!important` can beat a
  user-agent style and even a user style in some cases. Another reason to avoid
  it.
- **An inline `style` attribute beats all selectors** (a=1). A component that
  sets an inline style is un-overridable by class — another reason to prefer
  classes/custom properties.
- **`z-index` is not a competition**; it only compares within the same stacking
  context. A `z-index: 9999` inside a low-`z-index` parent still loses. Fix the
  stacking context (often an ancestor `transform`, `filter`, `opacity < 1`, or
  `will-change` creates one) and normalise `z-index` into a scale
  (`--z-dropdown: 10; --z-modal: 100;`) instead of magic numbers. This is the
  single most common "z-index doesn't work" bug.
- **`transform` and `filter` create a containing block / stacking context**,
  which silently changes both positioning and `z-index` behaviour. Animating
  `transform` on an ancestor is a frequent cause of "my fixed element moved".
- **A class with more selectors isn't "more important" if the other is in a
  later layer** — remember layers outrank specificity.
- **`:where()` with an argument still matches**; it just has zero specificity.
  You are not weakening the match, only the priority.
- **Media queries don't add specificity.** A media query only gates whether a
  rule applies; the specificity is the inside selector's.
- **CSS custom properties are inherited and lazy**; setting one on an element
  does not re-evaluate until something reads it, and a `--x` used before it's
  defined is invalid at computed-value time. Don't rely on definition order
  across shadow boundaries.
- **Reset is a layer, not a war.** Put your reset in the lowest layer so
  component styles beat it without extra specificity.
- **Dark mode via `prefers-color-scheme` and via `data-theme` should drive the
  same tokens**; don't write a second dark stylesheet — flip variables.

## A practical review checklist

- [ ] Does the codebase declare cascade layers, and is every file inside one?
- [ ] Is any rule more specific than one class without a documented reason?
- [ ] Are `!important`s confined to utilities/overrides layers (and enforced)?
- [ ] Do reusable components use container queries instead of viewport media
      queries where the component is width-dependent?
- [ ] Are component styles tokenised (no raw hex/spacing values in components)?
- [ ] Is there a z-index scale, and do animated ancestors avoid unexpected
      stacking contexts?
- [ ] Does the team have one primary strategy (Modules or utility), not both?

## Files

- `references/cascade-and-strategy.md` — expanded specificity rules, the
  layer-override recipe, and a when-to-use-Modules-vs-utility decision table.
