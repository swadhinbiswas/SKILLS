---
name: technical-writing-craft
description: Write technical prose that a busy expert can act on - inverted pyramid and BLUF, concrete examples, removing weasel words and hedges, explaining the why, and editing sentence by sentence. Use when writing or editing any technical document, runbook, RFC, design doc, or PR description, or when a user says the docs are unclear, verbose, or padded.
metadata:
  version: "1.0"
---

# Technical Writing Craft

Write for a competent engineer who is **new to this specific system** — not a
beginner, not a peer. They will scan first, stop if the answer is not near the
top, and leave if it is buried. Every principle below is a way to survive that
behaviour.

## BLUF: the answer first, always

**Bottom Line Up Front** is the whole document. Put the conclusion, the
recommendation, or the instruction in the first sentence, and put the
background after it, or nowhere.

- **Before:** "This document describes the considerations that led to the
  selection of a message broker for the events pipeline. Several candidates
  were evaluated over a two-week period, including managed and self-hosted
  options, against criteria of operational burden, delivery guarantees, and
  cost. After reviewing these factors and consulting the platform team, we
  recommend Kafka."
- **After:** "Use Kafka for the events pipeline. We rejected SQS and RabbitMQ
  because of replay and per-consumer offset requirements; the reasoning is
  below."

Same information, 62 words → 15. The reader who only needs the decision is
done in 15.

Structure: **conclusion → evidence → detail → caveats.** Everything else
(chronological, "background" first) makes the reader do your indexing for you.

## Inverted pyramid for a section

Each section is a small inverted pyramid: the most likely-needed fact first,
rarer cases after. Apply it to paragraphs, to procedures, and to the document.

```markdown
## Rolling back a release

If the release broke something, roll back. Use the dashboard script
(`bin/rollback.sh <service> <version>`); it drains and is reversible.

Only if the rollback script is unavailable: (rare, manual, 5 minutes)
  1. ...
```

The common case is first and self-contained. The rare case is still documented,
but it cannot be read by accident.

## Show, don't describe

Every claim about how something works should be followed by a thing that can be
run, read, or measured.

- **Before:** "The client retries failed requests with exponential backoff."
- **After:** "The client retries failed requests with exponential backoff:
  3 attempts, 250 ms base, 2× multiplier, 8 s cap, jitter ±20%. A `429` resets
  the backoff; a `500` does not. See `src/http/retry.py`."

- **Before:** "Results are cached for a short period."
- **After:** "Results are cached for 60 s in Redis, keyed by
  `search:<sha256(query)>`. Cache hits skip the rate limit but are counted
  separately in `search_cache_hits_total`."

The second version tells the reader what to expect, what to check, and where to
look. "Short period" is unusable.

## Weasel words and hedges: delete them

These words carry no information and signal that you have not made a call.
Strike them or make the call.

| Weasel | What it hides | Fix |
|---|---|---|
| "simply", "just", "obviously" | A step is not obvious | Do the step, or explain why it is safe |
| "note that", "be aware" | Something important is coming | Say the thing: "This overwrites the file." |
| "typically", "generally", "usually" | You don't know the actual behaviour | Measure it, or scope it: "In the three incidents we reviewed, …" |
| "may", "might", "could" | Is it true? | Pick one, or state the condition: "If the pool is exhausted, the request blocks for up to 5 s" |
| "various", "several", "some" | How many? | The number |
| "it is recommended that" | A requirement dressed as a suggestion | "Do X" or "Prefer X over Y because …" |
| "we believe", "arguably" | Your opinion, unlabelled | "I recommend X; the trade-off is Y" |
| "significantly", "substantially" | By how much? | "p99 dropped from 800 ms to 90 ms" |
| "etc." | The list is open | The list, or "…" deliberately |
| "as needed", "if necessary" | Your judgement is the reader's problem | A condition with a threshold |
| "please note" | Please | Delete |
| "in order to" | To | "to" |
| "leverage", "utilise" | Use | "use" |

The test: **could a reader act differently depending on which branch of the
hedge is true?** If yes, the hedge is hiding a decision you have not made.

## Explain the why, once

A reader who follows your instructions without understanding the reason will
break them the first time the situation is slightly different. One sentence of
"why" is worth three of "what".

```markdown
Run the migration with `--lock-timeout 5s`. Without it, a concurrent write
holds the ACCESS EXCLUSIVE lock and every write to this table blocks for the
duration of the migration (see INC-4402).
```

Do not explain the why for things that are self-evident (`git commit` does not
need a rationale). The rule: **explain why for every non-obvious instruction,
once, inline.** And never explain the same thing in three sections.

## Edit for a busy expert

- **One idea per sentence. One idea per paragraph, ideally.** If a paragraph
  needs "also", "however", or "moreover", it is two paragraphs.
- **Front-load the subject.** "The collector must run `memory_limiter` first"
  beats "In order for the collector to avoid being OOM-killed under load, it is
  important that…".
- **Use the reader's vocabulary, not your project's.** If the codebase says
  `checkout`, use `checkout`. Do not translate a function name into prose.
- **Tables for anything enumerable** (parameters, error codes, options,
  thresholds). Prose for anything with a sequence or a rationale.
- **Bold exactly three things per page.** Bold everywhere is bold nowhere.
- **Delete the throat-clearing**: "In this section, we will walk through…",
  "As previously mentioned", "It should be noted that".
- **Second person, active voice**: "Set the timeout", not "the timeout should
  be set". Instructions in the imperative; explanations can be indicative.
- **Cut by 30%**: every draft is 30% too long. Find the sentences that
  restate the previous one, the transitions, and the adjectives, and delete
  them. Most of what you cut will not be missed.
- **Read it aloud** for rhythm and for the sentences you would skip.

## Bad → good, the principles in practice

**Verbosity / nominalisation** — nouns standing where verbs should be:

- Before: "The implementation of the retry logic was implemented in a manner
  that resulted in the reduction of duplicate requests."
- After: "Retries are bounded, so a flaky upstream does not amplify load."
  (The original says nothing: "implementation … implemented".)

**Buried condition** — the important clause is in a subordinate position:

- Before: "The job retries, which will happen up to three times, with a delay
  between attempts, in the case where the error is retryable."
- After: "A retryable error retries up to 3 times, 250 ms apart, then fails the
  job. A non-retryable error (400, 422) fails immediately."

**Unquantified claim**:

- Before: "This significantly improved performance."
- After: "p99 latency dropped from 1.2 s to 140 ms over 24 h in staging
  (dashboard `latency-checkout`)."

**Vague referent**:

- Before: "This is faster than the old approach, but it requires more memory."
- After: "Scanning the index beats the table scan above 5k rows (12 ms vs 90 ms
  at 50k rows), and uses ~3× the memory (12 MB vs 4 MB per worker). It is the
  faster option below 5k rows, where the extra setup dominates."

**Empty preamble**:

- Before: "It is important to note that in many cases, there are situations
  where this approach can be problematic."
- After: Delete, and say the situation: "This fails when the input is larger
  than the 2 GB `max_allowed_packet`; see [Limitations]."

**Instructions with hidden steps**:

- Before: "Configure the collector. See the documentation for details."
- After: The three commands, the config snippet, and the one line that tells
  the reader how to know it worked.

**Passive hiding the actor**:

- Before: "It is recommended that the Idempotency-Key header be included in all
  POST requests."
- After: "Send `Idempotency-Key` on every `POST`. Without it, a network retry
  after a timeout creates a duplicate order — the server cannot tell a retry
  from a new request."

## Structure templates

**Runbook / procedure** — for someone mid-incident, half awake:

1. One-line symptom this covers.
2. The command to run, copy-pasteable, with `set -euo pipefail` and quoted
   expansions (`bin/rollback.sh <service> <version>`).
3. What success looks like (the exact output).
4. What to do when it fails (the next step, and the next step after that).
5. How to roll back / how to hand off.

**Design doc / RFC** — for a reviewer who will disagree with you:

1. Decision, in one sentence, first.
2. Context and constraints (with numbers).
3. The decision's shape, in enough detail to argue with.
4. Alternatives considered, and why each lost.
5. Consequences: what gets harder, who pays, and the follow-up work.
   (This is the `architecture-decision-records` format; reuse it.)

**Reference** — for someone looking up one fact:

1. The fact, in a table row or a short block.
2. Constraints and defaults.
3. The exception (rare, after the common case).
4. A link to the mechanism, not an explanation of it.

**PR description** — for a reviewer with a diff open:

1. What changed, in one or two sentences, and *why now*.
2. The behaviour change a user could notice (or "no user-visible change").
3. Anything you want reviewed specifically, and where.
4. How it was tested (the actual command and result).
5. Anything deliberately left out.

## The editing pass

Run this over any draft before it ships:

1. **Cut 10%** and see if anything was lost. Usually not.
2. **Find every hedge** (`may`, `might`, `typically`, `generally`, `should`,
   `could`, `some`, `various`, `arguably`). Make a call, or scope it to a
   condition. Repeat until none remain that hide a decision.
3. **Find every "note that", "in order to", "leverage", "please"**. Delete.
4. **Check the first sentence of every section** is the point of the section.
5. **Check every claim about behaviour** has a command, an output, or a
   reference next to it.
6. **Check every instruction** has its reason (once) and its failure mode.
7. **Read the first 100 words as if you were the reader deciding to stop.** If
   the answer is not there, rewrite the top.

## Gotchas

- **Brevity is not the goal; density is.** Cutting the reason, the constraint,
  or the failure mode makes a document shorter and worse. Cut redundancy, not
  information.
- **"Obviously" and "simply" are usually a signal the *writer* did not
  understand the step**, not that the reader is slow. When you catch yourself
  writing them, go verify.
- **Hedging is sometimes correct.** "Usually" is right when the behaviour is
  genuinely version-dependent. The fix is to say *why* it varies
  ("in Postgres < 12, …"), not to delete the qualifier.
- **Second person is not the same as imperative for everything.** "You must
  set the timeout before starting the job" is better than "Set the timeout
  before starting the job" only when the consequence of forgetting is severe
  and irreversible; otherwise the imperative is fine.
- **Explaining the why once does not mean once per document audience.** A
  runbook and a design doc are different audiences for the same system; each
  needs its own explanation, and cross-linking is not a substitute when the
  reader may never open the other document.
- **A document that is only correct-by-construction is not documentation.** A
  linter that generates the config table fixes rot, not a hand-written table
  that will drift. Generate reference material; write everything else.
- **Style guides are not laws.** Match the surrounding document's conventions
  unless they are actively harmful; a new house style applied to one file in an
  old repo is just a diff.

## Files

- Read `references/editing-worked-examples.md` when you are rewriting a
  specific section and want more before/after pairs covering procedures,
  reference entries, and design docs.
