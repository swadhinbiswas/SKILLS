# WCAG checklist (2.1 / 2.2, level AA)

Load this for a real audit pass. Grouped by the four WCAG principles, with the
failure each criterion catches and the fix. Level A is mandatory; AA is the
usual legal/digital-standard target (WCAG 2.2 adds 2.4.11 focus-not-obscured,
3.3.7 redundant entry, 3.3.8 accessible authentication; 2.5.8 target size is
AAA — don't claim it at AA).

Test order that catches the most: keyboard pass first (it finds ~half the
issues), then screen reader, then contrast, then zoom.

## 1. Perceivable

### 1.1 Text alternatives

| Criterion | Catches | Fix |
|---|---|---|
| 1.1.1 Non-text Content (A) | Images with no/misspelt `alt`; icon buttons with no name; charts with no description | Meaningful image → `alt` describing purpose; decorative → `alt=""`; icon-only button → `aria-label`; complex image → adjacent text/longdesc pattern |
| 1.1.2 Audio Description / Video Captions (A) | Video without captions | `<track kind="captions">`; audio description for essential visual info; mark decorative media `aria-hidden` + transcript |

### 1.2 Time-based media

- 1.2.1–1.2.5 (A): captions, audio description, alternatives for
  prerecorded audio/video. For a product video, a transcript is usually the
  pragmatic AA path.

### 1.3 Adaptable

| Criterion | Catches | Fix |
|---|---|---|
| 1.3.1 Info and Relationships (A) | Layout tables, skipped heading levels, list markup absent, unlabelled groups | Real headings, `<ul>/<ol>/<li>`, `<table>` for tabular data, `<fieldset><legend>` |
| 1.3.2 Meaningful Sequence (A) | DOM order not matching visual order (CSS reordering with flex `order`/grid) | Keep DOM order = visual order; avoid `order`/`row-reverse` for reading-order content |
| 1.3.3 Sensory Characteristics (A) | "Click the round button on the right" | Refer to shape/colour/position by name, not alone |
| 1.3.4 Orientation (AA) | Layout locked to portrait/landscape | Support both; don't block zoom |

### 1.4 Distinguishable

| Criterion | Catches | Fix |
|---|---|---|
| 1.4.1 Use of Color (A) | Colour-only errors, links distinguished only by colour | Add text/icon/underline; error text + icon |
| 1.4.3 Contrast (Minimum) (AA) | Grey placeholder text, low-contrast buttons | Text ≥4.5:1, large text ≥3:1 (Lighthouse/axe) |
| 1.4.4 Resize Text (AA) | Text clipped when zoomed | rem sizing, no fixed px heights on text containers |
| 1.4.5 Images of Text (AA) | Text baked into images | Real text |
| 1.4.10 Reflow (AA) | Horizontal scroll at 320px width / 400% zoom | Responsive layout, no fixed widths |
| 1.4.11 Non-text Contrast (AA) | Faint borders, icons, focus ring, form control outlines | UI components and focus indicators ≥3:1 |
| 1.4.12 Text Spacing (AA) | Text clipped when user increases line/letter/word spacing | Don't fix heights; allow reflow |
| 1.4.13 Content on Hover or Focus (AA) | Tooltips that trap, don't dismiss, or aren't hoverable | Dismissible with Escape, hoverable, persistent |
| 1.4.3 via 2.2: 1.4.10, 1.4.11, 1.4.12 as above | | |

Motion: honour `prefers-reduced-motion` (1.4.3-adjacent best practice; WCAG
2.2 adds 2.3.3 Animation from Interactions at AAA, but respect it anyway).

## 2. Operable

### 2.1 Keyboard

| Criterion | Catches | Fix |
|---|---|---|
| 2.1.1 Keyboard (A) | `div onclick`, mouse-only drag, custom widget with no key handling | Native buttons/links; or full keyboard support on custom widgets |
| 2.1.2 No Keyboard Trap (A) | Carousels/editors that eat Tab forever | Always provide a way out (Esc, or visible focusable element outside) |
| 2.1.4 Character Key Shortcuts (A) | Single-letter shortcuts that fire while typing | Gate behind modifier or off-by-default |

### 2.2 Enough Time

- 2.2.1 (A): allow timeouts to be extended/disabled (or none).
- 2.2.2 (A): pause/stop/hide auto-moving, auto-updating, blinking content.
- Session timeouts: warn, allow extension.

### 2.3 Seizures and Physical

- 2.3.1 (A): no content flashing >3×/second.
- 2.3.3 (AAA) / best practice: `prefers-reduced-motion` for non-essential
  animation.

### 2.4 Navigable

| Criterion | Catches | Fix |
|---|---|---|
| 2.4.1 Bypass Blocks (A) | No skip link; many nav items before main | Skip link to `#main`; landmarks |
| 2.4.2 Page Titled (A) | Static title on an SPA | Per-route `document.title` |
| 2.4.3 Focus Order (A) | Positive `tabindex` scrambling order; modal not trapping | DOM order = focus order; never positive tabindex except keyboard-wrapping patterns |
| 2.4.4 Link Purpose (in context) (A) | "Click here", "Read more" x5 | Descriptive link text or `aria-label` |
| 2.4.5 Multiple Ways (AA) | Single nav | Nav, search, sitemap |
| 2.4.6 Headings and Labels (AA) | Generic "Section"; form with no legend | Descriptive headings/labels |
| 2.4.7 Focus Visible (AA) | `outline: none` with no replacement | `:focus-visible` ring, ≥3:1 contrast |
| 2.4.11 Focus Not Obscured (2.2, AA) | Sticky header covers the focused element | `scroll-margin` / offsets so focus scrolls clear of sticky UI |
| 2.4.13 Focus Appearance (2.2, AAA) | Cosmetic; don't gate on it at AA | Nice-to-have |

### 2.5 Input Modalities

- 2.5.1 Pointer Gestures (A): multi-point/path gestures need a single-pointer
  alternative.
- 2.5.2 Pointer Cancellation (A): act on `click`/up, not `down`.
- 2.5.3 Label in Name (A): the accessible name must contain the visible label
  ("Submit" button labelled "Send form" fails).
- 2.5.4 Motion Actuation (A): shake-to-undo needs a button alternative.
- 2.5.7 Dragging Movements (2.2, AA): drag-and-drop needs a non-drag
  alternative (move up/down buttons).
- 2.5.8 Target Size (2.2, **AAA**): 24px minimum — often enforced in EU
  practice (EN 301 549 / EAA references it); treat as best practice.

## 3. Understandable

### 3.1 Readable

- 3.1.1 Language of Page (A): `<html lang>`; mark language changes.
- 3.1.2 Language of Parts (AA): `lang` on foreign passages.

### 3.2 Predictable

| Criterion | Catches | Fix |
|---|---|---|
| 3.2.1 On Focus (A) | Focus triggers navigation/modal | Focus alone must not change context |
| 3.2.2 On Input (A) | Selecting an option instantly submits/navigates | Wait for explicit submit, or announce before changing context |
| 3.2.3 Consistent Navigation (AA) | Nav reorders between pages | Keep it stable |
| 3.2.4 Consistent Identification (AA) | Same control labelled differently | Consistent names |
| 3.2.6 Consistent Help (2.2, A) | Help mechanisms in a consistent place | |

### 3.3 Input Assistance

| Criterion | Catches | Fix |
|---|---|---|
| 3.3.1 Error Identification (A) | Unlabelled errors; colour-only | Text error, tied to field, `aria-invalid` |
| 3.3.2 Labels or Instructions (A) | Placeholder-as-label; no format hint | `<label for>`; `aria-describedby` for help/format |
| 3.3.3 Error Suggestion (AA) | "Invalid input" with no fix | Suggest the correction |
| 3.3.4 Error Prevention (Legal/Financial) (AA) | Irreversible action, no confirm/review | Confirmation, review step, undo |
| 3.3.7 Redundant Entry (2.2, A) | Re-entering the same info in a multi-step flow | Auto-populate or let the user select from prior input |
| 3.3.8 Accessible Authentication (2.2, AA) | Forcing CAPTCHA/transcription on login | Allow paste, allow alternative, no cognitive function test unless alternative exists |

## 4. Robust

| Criterion | Catches | Fix |
|---|---|---|
| 4.1.2 Name, Role, Value (A) | Custom widget with no role/state; `div` buttons; `aria-*` contradicting the element | Native elements; correct role + `aria-expanded`/`checked`/`pressed`; keep state accurate |
| 4.1.3 Status Messages (AA) | Search results/toasts/save-status not announced | `role="status"`/`aria-live="polite"` present in the DOM before the update; `role="alert"` for errors |

## Audit pass order

1. **Keyboard** (2.1, 2.4.3, 2.4.7, 2.1.2): unplug the mouse, tab the whole
   page, open/close every overlay, watch focus.
2. **Automated scan** (Lighthouse/axe): contrast, missing alt/labels/ARIA,
   duplicate ids, landmark structure.
3. **Screen reader** (4.1.2, 4.1.3, 1.3.1): VoiceOver/NVDA/TalkBack through the
   primary flow; check names, roles, states, and that updates are announced.
4. **Zoom/reflow** (1.4.4, 1.4.10, 2.4.11): 200%/400% zoom, 320px width, long
   content; check sticky headers don't hide focus.
5. **Forms** (3.3.x): submit empty, submit invalid, check labels, error text,
   `aria-describedby`, error summary links.
6. **Motion & media** (2.2.2, 2.3.1, 1.2.x): reduced motion, captions, no
   auto-playing-with-sound.

Fix A-level before AA-level; the A failures are the ones that make a UI
genuinely unusable, not just non-conformant.
