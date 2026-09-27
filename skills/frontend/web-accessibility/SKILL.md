---
name: web-accessibility
description: Make a web UI genuinely usable with a keyboard and a screen reader - semantic HTML first, focus management, ARIA only where HTML fails, colour contrast, form labelling, and live regions - with a manual audit checklist and the real-world failures that reject audits (modals, menus, custom selects, focus loss on route change). Use when building or fixing a11y, when a screen reader user is blocked, when a modal or dropdown traps focus, when an automated audit reports violations, or before a launch that requires WCAG conformance. Triggers on "accessibility", "a11y", "WCAG", "screen reader", "keyboard navigation", "focus trap", "aria-label", "alt text", "color contrast", "axe", "Lighthouse accessibility", "tab order", "role".
compatibility: Standards-based (HTML/ARIA); WCAG 2.2 is the current recommendation, with WCAG 2.1 as the widely-enforced baseline.
metadata:
  version: "1.0"
---

# Web Accessibility

Accessibility is mostly **using the right HTML**. ARIA is a patch for when
native elements cannot express the widget, not a substitute for a `<button>`.
The fastest way to an accessible UI is a semantic DOM; the fastest way to an
inaccessible one is a `<div onclick>`.

Target **WCAG 2.2 AA** unless a regulation says otherwise (many — ADA, EU
EAA, Section 508, and public-sector rules in several countries — require AA and
are legally enforceable).

## The order of operations

1. **Semantics.** Use the native element that already has the role, keyboard
   behaviour, and state. Most issues end here.
2. **Accessible name and description.** Every interactive element needs a name
   that conveys its purpose.
3. **Keyboard.** Everything operable by mouse is operable by keyboard, in a
   sensible order, with a visible focus indicator.
4. **Contrast and colour.** Text ≥4.5:1, large text ≥3:1, UI/focus ≥3:1; never
   colour alone.
5. **ARIA for the rest.** Only for custom widgets; get the roles, states, and
   keyboard model right.

## Semantics first

| Instead of | Use | Why |
|---|---|---|
| `<div onclick>` | `<button type="button">` | Free role, Enter/Space, focusable, announced |
| `<a onclick>` navigating | `<a href="…">` | Link role, middle-click, right-click menu |
| `<div class="checkbox">` | `<input type="checkbox">` (visually styled) | Free state (`aria-checked`/checked) |
| `<span role="button">` | `<button>` | Focus and keyboard for free |
| `<img>` for a button icon | `<button aria-label="Close">` | Name from label |
| Headings as text styling | `<h1>…<h6>` in order | Structure/navigation for screen readers |
| `<table>` for layout | CSS grid/flex | Layout tables confuse screen readers |
| Placeholder as label | `<label for>` | Placeholders disappear and have poor contrast |

Two things native elements give you that you must otherwise hand-build:
**keyboard activation** and **correct role/state announced by the screen
reader**. A custom widget missing either is a bug.

## Keyboard: the contract

- **Tab / Shift+Tab** move focus forward/back through interactive elements in
  DOM order. Make DOM order match visual order.
- **Enter** activates buttons/links; **Space** toggles buttons/checkboxes;
  **Enter** submits forms.
- **Escape** closes dialogs, menus, popovers.
- **Arrow keys** move within composite widgets: tabs, menus, listboxes,
  radio groups, grids.
- **`Tab` should not be trapped** except inside a modal dialog, where trapping
  is the point.
- Every focusable element needs a **visible focus indicator** that isn't
  removed. If you hide the default outline, replace it:
  ```css
  :focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
  ```
  (Never `outline: none` without an equal-or-better replacement. Keep
  `:focus-visible` so mouse users don't see rings, but keyboard users always do.)
- Disabled controls: a truly `disabled` button is not focusable (use
  `aria-disabled="true"` + keep it focusable if the user must discover *why*
  it's unavailable).

### Focus management — the part teams forget

- **On mount, move focus** to the new content (a dialog, a wizard step, an
  error summary).
- **On unmount, return focus** to the trigger, or the nearest sensible ancestor.
  If you don't, focus falls to `<body>` and the keyboard user restarts at the
  top of the page.
- **Modals**: on open, focus the first focusable (or the dialog itself); trap
  Tab inside; on close, restore focus to the trigger. Implement this yourself
  or use a component library that does it (Radix, Headless UI, React Aria).
- **Route changes (SPA)**: after navigation, move focus to the new `<h1>` or a
  skip target and announce the change. Otherwise the screen reader user is
  still "on" the old page. This is the single most common SPA a11y failure —
  focus silently stays where it was or resets unpredictably.
- **Deleting the focused element**: move focus to a sensible neighbour, not
  nowhere.

```tsx
// Route change: focus the new heading.
const { pathname } = useLocation();
const heading = useRef<HTMLHeadingElement>(null);
useEffect(() => { heading.current?.focus(); }, [pathname]);
// …<h1 ref={heading} tabIndex={-1}>New page</h1>
```

## ARIA, only where HTML fails

ARIA changes what a screen reader announces and nothing else. It does not add
behaviour. Adding `role="button"` to a `<div>` does not make Enter/Space work or
make it focusable — you still owe it `tabindex="0"` and key handlers. Prefer the
native element and skip all of it.

- **The first rule of ARIA**: don't use ARIA. Second rule: if you must, use a
  native element. The `aria-*` you are most likely to need: `aria-label`,
  `aria-labelledby`, `aria-describedby`, `aria-expanded`, `aria-controls`,
  `aria-current`, `aria-pressed` (toggle buttons), `aria-haspopup`.
- **`aria-label` overrides the content.** On a `<button>` with text,
  `aria-label` *replaces* that text for the screen reader — if they differ, the
  label wins, which is a bug magnet. Prefer visible text over `aria-label` when
  there is visible text.
- **A native `<select>` is better than a custom listbox** (less code, mobile
  picker, correct announcement). Build a custom one only if the design demands
  it, and then it must be a full `role="listbox"`/`option` (or `menu`) widget
  with arrow-key, Home/End, type-ahead, and `aria-activedescendant` or
  roving `tabindex`.
- **Toggle vs checkbox.** A button that toggles: `aria-pressed="true|false"`.
  A checkbox: `<input type="checkbox">` (or `role="checkbox"` +
  `aria-checked`). Don't confuse them.
- **Tabs**: `role="tablist"`, `role="tab"` with `aria-selected`, arrow-key
  navigation, and each panel `role="tabpanel"` linked via `aria-controls` /
  `aria-labelledby`.
- **Never** put `role` on a `<div>` that should just be the right element, and
  never add a role that contradicts the element (e.g. `role="button"` on an
  `<a href>`).

## Colour and visual

- **Contrast**: body text ≥ **4.5:1**, large text (≥18.66px bold or ≥24px)
  ≥ **3:1**, UI components and focus indicators ≥ **3:1** (WCAG 2.1 AA).
  Check with a contrast tool or Lighthouse; `opacity`, greys-on-white, and
  placeholder text are the usual offenders.
- **Never colour alone**: errors need text/icon too, not just red; charts need
  patterns/labels.
- **Text zoom**: support 200% browser zoom and `rem`-based sizing; don't fix
  `px` heights that clip.
- **`prefers-reduced-motion`**: disable non-essential animation.
  ```css
  @media (prefers-reduced-motion: reduce) {
    * { animation-duration: .01ms !important; transition-duration: .01ms !important; }
  }
  ```
- **Don't disable zoom** (`user-scalable=no` in the viewport meta) — WCAG 1.4.4.
  Correct viewport meta: `width=device-width, initial-scale=1`.

## Forms

- **Every field has a `<label for="id">`.** Placeholders are not labels
  (they vanish on input and often fail contrast).
- **Group related fields** in a `<fieldset>` with a `<legend>` (radio sets,
  address blocks).
- **Errors**: identify the field in text, tie it with `aria-describedby`, and
  set `aria-invalid="true"`. Put a summary at the top on submit that links to
  the failing fields.
- **Required**: mark with the `required` attribute (and a visible "required"
  indicator, since `required` is not always announced).
- **Autocomplete**: use `autocomplete` attributes (`email`, `name`, `one-time-code`)
  — WCAG 1.3.5 and a huge real-world usability win.
- **Don't disable the submit button** on an invalid form; let the user submit
  and show what's missing. A permanently disabled button gives no feedback.
- **Buttons say what they do** ("Save changes"), not "Submit"/"OK" in a dialog
  that needs context.

## Images and media

- **Meaningful images**: `alt` describing the *function* ("Search results
  settings"), not "image of a chart" — or `alt=""` if purely decorative.
- **Decorative** images: `alt=""` (empty, not omitted) so they're skipped.
- **Complex images** (charts, diagrams): a text alternative nearby or a
  `figcaption`/long description — `alt` is not a caption.
- **Video**: captions (`<track kind="captions">`) for audio; if the video is
  decorative, mark it `aria-hidden` and provide a transcript.
- **Icon-only buttons**: `aria-label="Close"` (or visually-hidden text).

## Live regions (announcing dynamic change)

A live region is a container you put text into; screen readers announce changes
to it. It must **exist in the DOM before the content changes** — a region added
at the same time as its text is often not announced.

```html
<div aria-live="polite" role="status">       <!-- for status, waits for a pause -->
  <p>{statusMessage}</p>
</div>
<div aria-live="assertive" role="alert">…</div>  <!-- for errors, interrupts -->
```

- Use `polite` (default) for non-urgent updates ("3 results", "Saved");
  `assertive`/`role="alert"` only for errors that must interrupt.
- **Don't announce every keystroke** — debounce search-result announcements.
- Re-rendering a live region wholesale can double-announce; keep it stable and
  change only the text node.
- Announce route changes, save/save-failed, toasts, item counts.

## Dynamic titles and landmarks

- One `<h1>` per page; heading levels shouldn't skip.
- Landmarks (`<header>`, `<nav>`, `<main>`, `<footer>`, `<aside>`) let users
  jump; give `<nav>` an accessible name if there are several
  (`aria-label="Primary"`).
- **Skip link** as the first focusable element:
  ```html
  <a class="skip-link" href="#main">Skip to main content</a>
  ```
  (visually hidden until focused).
- Set `document.title` per route — the screen reader announces the title on
  navigation.
- `aria-current="page"` on the active nav link.

## Common real-world failures (the ones that block people)

1. **Modal not trapping focus / not restoring it.** Tab escapes to the
   background; on close, focus is lost. Use a dialog component with a focus
   trap, `role="dialog" aria-modal="true"`, labelled by its title, Escape to
   close, and focus restore.
2. **Custom menu or select that isn't keyboard-operable.** Needs
   `role="menu"`/`menuitem` or `listbox`/`option`, arrow keys, Escape, and
   focus management. Most custom dropdowns fail all of these.
3. **Focus lost on SPA route change** (see above) — the #1 SPA failure.
4. **Icon buttons with no name** (icon-only, no `aria-label`) — announced as
   "button" with no purpose.
5. **Placeholder-as-label** and missing form labels.
6. **Off-canvas / hamburger menu** that doesn't manage focus or announce
   expanded state (`aria-expanded`, `aria-controls`).
7. **Colour-only errors / low contrast** grey text.
8. **Images without alt**, or `alt` that says "image".
9. **Skeleton/loading without `aria-busy`/live announcement** — the user
   doesn't know content arrived.
10. **Disabled controls as the only feedback**, and tooltips as the only
    label.
11. **Keyboard traps** in a non-modal widget (a carousel or editor that eats
    Tab with no way out) — worse than broken focus; users get stuck.
12. **Custom checkbox/radio/toggle** that isn't `<input>` and lacks
    `aria-checked`/keyboard.

## Manual audit checklist (do this; tools can't)

Tools (axe, Lighthouse) catch ~30–40% of issues. The rest need a keyboard and
eyes. Load `references/wcag-checklist.md` for the full pass/fail checklist with
per-criterion references.

- [ ] Unplug the mouse. Tab through the whole page: can you reach everything?
      Is order logical? Is focus always visible?
- [ ] Activate every control with Enter/Space/arrows/Escape as appropriate.
- [ ] Open and close every overlay with Escape; confirm focus is trapped and
      restored.
- [ ] Navigate to each route: does focus/announcement move to the new content?
- [ ] Zoom to 200% and 400%: no horizontal scroll, nothing clipped.
- [ ] Check contrast of text, placeholders, borders, focus rings.
- [ ] Turn on a screen reader (VoiceOver `Cmd+F5`, NVDA, TalkBack) and listen
      to the primary flow: are names, roles, states, and updates correct?
- [ ] Disable images/CSS: does content and function survive?
- [ ] Check `prefers-reduced-motion` and `prefers-color-scheme`.

## Automated tooling (a floor, not a ceiling)

- **Lighthouse / axe DevTools** for a quick pass; **axe-core** or
  `@axe-core/react` in CI to gate merges.
- **eslint-plugin-jsx-a11y** for the common React mistakes.
- These catch missing `alt`, bad ARIA, contrast, missing labels — but **not**
  focus order, focus management, or whether the flow makes sense.

## Gotchas

- **Focusing a programmatic target needs `tabindex="-1"`.** `heading.focus()` on
  an `<h1>` with no `tabindex` is a no-op in most browsers, so the route-change
  fix silently does nothing. `tabindex="-1"` is programmatic-only: it stays out
  of the Tab order.
- **A native `<dialog>` opened with `showModal()` moves focus and traps it for
  you; opening it by setting the `open` attribute does not.** The second form
  is an ordinary element in the flow — Tab walks straight out of it and focus is
  never restored on close. Use `showModal()` / `close()`, or a component library.
- **`aria-modal="true"` on a background page element does not trap Tab and
  sometimes makes worse software hide the rest of the page from the screen
  reader entirely** with no keyboard to escape from. Move the modal to a portal
  and trap focus in a keydown handler.
- **`inert` is the accessible replacement for hand-rolled focus traps, and only
  where supported** (every current engine; older ones need the `aria-hidden` +
  `tabindex` dance). Setting `inert` on the app root while the dialog is open
  removes everything behind it from focus and the accessibility tree in one
  attribute.
- **`aria-hidden="true"` on an element containing a focusable descendant is a
  hard WCAG failure** (1.3.1 / 4.1.2), and it is how icon libraries break a page.
  Screen readers skip it, sighted keyboard users land in it. Use `inert`, or
  `display: none`.
- **A live region created in the same render as its text is usually never
  announced.** The region has to be in the DOM, and the assistive technology has
  to have registered it, before the text node changes. Render the empty region
  first, then set the message; changing a region's `aria-live` value after
  mount does not re-trigger announcements.
- **An assertive region is not just louder, it is a queue-jumper** — it
  interrupts the user's speech and they lose what they were listening to. One
  `role="alert"` per form submit, never one per validation keystroke; for
  everything else use `polite` and debounce.
- **Grey placeholder text and `opacity`-faded helper text fail contrast even
  when the label next to them passes.** `#999` on white is about 2.8:1, well
  under the 4.5:1 required for anything under 24px; the same value is fine on
  a 4.5:1 background and fails on a lighter one, so check each pair, not the
  palette.
- **Focus indicators are held to 3:1 against *adjacent* colours**, including the
  background behind the element. A 2px ring at 1.8:1 on a white card is a WCAG
  1.4.11 failure even though it "looks visible" in a design review. `outline`
  with `outline-offset` also survives forced-colors/high-contrast mode;
  `box-shadow` does not.
- **A `title` attribute is not a label and a `placeholder` is not a label.**
  `title` is a tooltip that only appears on hover, is unreachable by keyboard or
  touch, and is skipped by several screen readers. Every control needs visible
  text, `<label for>`, `aria-label`, or `aria-labelledby` — nothing else counts.
- **Focus management on navigation must be driven by the router's location, not
  by the nav link's click handler, and must skip the initial mount.** A handler
  never fires on Back (`popstate` is the only signal that does), so a
  screen-reader user is stranded on the old page; an unguarded `useEffect` that
  focuses on mount also yanks focus to the top of the page out from under
  someone who merely tabbed in. Observe `location`, and skip the first run.
- **Dismissing a dialog by removing it from the DOM leaves focus on `<body>`.**
  Capture the trigger in a `ref` at open time (not at close time — by then it may
  have unmounted) and restore it in the cleanup.

## Review checklist

- [ ] Interactive elements are native (`<button>`, `<a>`, `<input>`), not
      `div`s with handlers.
- [ ] Every control has an accessible name; every field a real `<label>`.
- [ ] Focus is visible, ordered, and managed on route change and in overlays.
- [ ] Overlays trap focus, close on Escape, and restore focus.
- [ ] Dynamic changes are announced via live regions (debounced).
- [ ] Contrast meets 4.5:1 / 3:1; no colour-only meaning.
- [ ] One `<h1>`, landmarks, skip link, per-route `document.title`.
- [ ] The whole primary flow works keyboard-only, and a screen reader
      announces it sensibly.

## Files

- `references/wcag-checklist.md` — WCAG 2.1/2.2 AA success-criterion
  checklist, mapped to the failure it catches and how to fix it.
