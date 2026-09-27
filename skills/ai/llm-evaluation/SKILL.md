---
name: llm-evaluation
description: Build and run evals for LLM features - golden datasets mined from real traffic, LLM-as-judge with its biases and calibration, deterministic vs model-graded tests, pairwise comparison over scoring, regression gates in CI, and rubric design. Use when a prompt or model change needs to be proven not to regress, when someone asks "how do we know it got worse", "LLM-as-judge", "golden set", "eval harness", "regression testing prompts", or "how do we measure quality".
compatibility: Framework-agnostic. Judge prompts and APIs change; pin the judge model and version in config.
metadata:
  version: "1.0"
---

# LLM Evaluation

An LLM feature without evals is a feature you cannot change. Prompts,
models, and chunking all change under you, and nothing fails loudly — quality
just drifts. Evals are the only way to make the change a decision instead of a
gamble.

## What you are measuring

Always measure one of these, explicitly. "Quality" is not measurable; these
are.

| Question | Metric type | Cheap? |
|---|---|---|
| Did it pick the right intent/label? | Deterministic, exact match / F1 | Yes |
| Is the JSON schema valid? | Deterministic validator | Yes |
| Is the answer factually right? | Exact / F1 on short answers, or judged | Mixed |
| Is the answer supported by the context? | Judged (groundedness) | No |
| Is it helpful / well-written / on-brand? | Judged rubric | No |
| Did it beat the old prompt? | Pairwise judged, human-labelled subset | No |

## Build the golden dataset

This is the deliverable. The prompt is not.

- **Start with 50-100 real inputs**, mined from logs, tickets, or production
  traces. Real phrasing is dramatically harder than anything you write from a
  doc, and it is the only distribution your users have.
- **Stratify by difficulty and length.** Include the ugly tail: empty input,
  10k-token input, mixed language, adversarial input, ambiguous input, and the
  inputs that broke the feature last week.
- **Label what "correct" means per case**, before running anything. For
  classification that is a label. For generation it is a reference answer, a
  set of required facts, or a rubric with per-case criteria.
- **Split by scenario, not randomly by row.** Random splits leak near-duplicates
  between train/dev and inflate scores. If a user or a document can appear in
  both, it is not a split.
- **Version the dataset in the repo** as JSONL with a stable `id`, and grow it
  with every production failure. A fixed eval set that never changes only
  measures regression, not capability.
- **Label 2x where it's cheap.** Double-annotating 50 cases gives you a
  human-human agreement number; without it you cannot say whether 85% from a
  judge means anything.

```jsonl
{"id":"refund-014","input":"where is my refund lol","context":"<doc id=policy-2>...</doc>","expected":{"intent":"refund_status","must_include":["3-5 business days"],"must_not":["guarantee date"]},"difficulty":"medium","source":"prod-log-2026-03-04"}
```

## Deterministic tests first

Free, exact, and they catch the majority of regressions. Put them in normal CI,
on every PR, because they cost pennies:

- Schema validity, required fields, enum membership, `max_length` bounds.
- Classification: exact match, macro-F1, per-class precision/recall. A single
  accuracy number hides a collapsed rare class.
- Refusal behaviour: on a set of must-refuse inputs, assert the output is a
  refusal. Assert this every run — it is a safety property, not a quality one.
- Retrieval: Recall@k, MRR (see `skills/ai/llm-rag-systems/SKILL.md`).
- Format-sensitive regexes for machine-parsed sections.
- Cost and latency ceilings: fail the build if p95 tokens or p95 latency
  regressed past a threshold. Cheap tests that catch expensive regressions.

## LLM-as-judge

Use it for what deterministic checks cannot express. It is a *measurement
instrument*, and instruments need calibration.

- **Rubric first, then judge.** Write the criteria before writing the prompt.
  Criteria should be specific and checkable: "every factual claim is
  traceable to a numbered source", "no commitment to a delivery date", not
  "is the answer good".
- **One dimension per call.** "Rate helpfulness" gives you an unusable
  average. Rate groundedness, then completeness, then tone, in separate calls
  (or a structured multi-field rubric you aggregate explicitly).
- **Score on a 1-5 anchored scale with written anchors for every level**, not
  1-10. Anchors are what make judge scores comparable across runs.
- **Return structured output**, not prose. A judge asked to "explain your
  rating" without a schema returns a paragraph you have to parse and whose
  rating you cannot trust.
- **Low temperature, pinned model, pinned prompt version.** Log all three with
  every score. An unpinned judge silently changes your metric between runs and
  the drift reads as product change.
- **Include the reason in the output** and read a sample of them: the reasons
  tell you *why* quality moved, which the score alone cannot.

### Judge biases to control for

| Bias | What it does | Control |
|---|---|---|
| Verbosity | Prefers longer, more thorough answers | Explicitly instruct length neutrality; compare length across arms |
| Self-preference | Prefers its own model's style | Blind arm labels; never let the judge see which model produced what |
| Position | Prefers the first or the last option in a list | Randomise order per item |
| Authority | A confident wrong answer beats a hedged right one | Anchor the rubric on evidence, not confidence |
| Agreement | Inflates agreement with the human label | Measure judge-vs-human agreement on your labelled set; report the number |
| Format | Penalises answers that do not match the judge's preferred shape | Pin the judge's output schema and strip formatting from what it sees |

### Calibrate before you trust

- Take 100-200 human-labelled cases, run the judge on the same cases, and
  compute agreement (per class for classification, Spearman or a binned
  confusion matrix for scores).
- Judge agreement with humans of 0.7-0.8 on a binary task is often good enough
  to steer. Below ~0.6, the judge is measuring its own preferences — do not
  gate CI on it.
- **Sample the disagreements and read them.** The failure patterns tell you what
  to fix in the rubric.
- Re-run calibration when you change the judge model, the judge prompt, or the
  product's output style.

## Pairwise comparison beats absolute scoring

For "is this prompt better than that one", ask for a preference, not a score.

- Show both answers to a judge with the user query and context, **without
  labels, in randomised order**, and ask which better satisfies the rubric.
- Report a win rate with a confidence interval. A 52/48 result is noise; do not
  ship on it. Require roughly 60%+ and a sample size in the low hundreds for a
  call you care about.
- Reserve absolute scores for tracking a single system over time. Relative
  judgement is more reliable than absolute.
- Keep a small human-labelled pairwise set as the anchor for the judge.

## Regression testing in CI

```
on every PR touching prompts/, model config, or retrieval:
  1. run deterministic suite        -> must pass, blocks merge
  2. run eval set (fixed sample)   -> score delta vs main must be > -2%
  3. run judge sample (200 cases)  -> report; block only on large drops
  4. cost/latency ceilings          -> must pass
```

- **Gate on deltas, not absolutes.** A system at 71% that was 73% yesterday
  needs a decision; a system at 71% that was 68% last month needs none.
- **Use a two-tier design**: a small fixed set (100-200 cases) on every PR, and
  a large nightly set (1k+) with judge grading, which catches rare regressions
  the small set misses.
- **Pin everything in the run record**: prompt version, model id, sampling
  params, temperature, dataset version, judge version. A score without these is
  not reproducible.
- **Flakiness is real** because the system under test is stochastic. Set
  temperature to 0 for scored runs, run duplicates on a small subset to measure
  noise, and set the gate threshold *above* the measured noise.
- **Never let an eval suite call a live model in a way that costs real money
  without a budget cap and a kill switch.**

## Eval your evals

Your eval harness is code and will be wrong. Budget for checking it:

- **Leakage check**: is any test case answerable from the prompt itself? Is a
  label present in the input? Sanity-check by feeding a deliberately broken
  model output and confirming the suite fails.
- **Discrimination check**: a good eval set separates a good system from a bad
  one. If a garbage baseline scores 80%, the set is not measuring anything.
- **Stability check**: run the same set twice at temperature 0 and measure
  variance. High variance means your gate threshold is fiction.
- **Drift check**: track the pass rate on the fixed set over months. A slow
  decline means the product changed, or the world did.
- **Coverage check**: which real production inputs are *not* represented? Sample
  100 recent requests and diff against the dataset ids.

## Gotchas

- **Test sets become the product.** Optimising hard against 200 cases
  overfits: you tune the prompt to the set, not the task. Keep a held-out split
  you do not iterate against.
- **"Pass rate" hides per-segment regressions.** Report per-class, per-length,
  per-language. A +2% overall can be a 15% regression on your largest non-English
  segment.
- **Self-reported quality metrics from demos are not measurements.** No eval
  set, no claim.
- **Reference answers expire** as the product changes. Re-review goldens
  periodically; a stale golden measures your old product.
- **Judge and grader prompt changes are model changes.** Version them with the
  same discipline as product prompts.
- **Auto-generated goldens inherit the generator's biases** and will happily
  agree with the model. Use them to *grow* the set, human-label a random sample
  to validate, and never fully auto-accept them.
- **A metric you do not report will not be used.** Put the headline number in
  the PR description.

## Checklist

- [ ] 100+ real inputs, stratified by difficulty and length, versioned
- [ ] Per-case labels or rubrics written before running anything
- [ ] Deterministic suite runs in normal CI and blocks merges
- [ ] Judge prompt anchored, low-temperature, model and version pinned
- [ ] Judge-vs-human agreement measured and reported
- [ ] Gates are on deltas from `main`, with thresholds above measured noise
- [ ] Prompt/model/dataset/judge versions recorded with every score
- [ ] Held-out split exists and is not used for iteration
- [ ] Nightly large-scale run catches rare regressions
- [ ] Harness itself checked for leakage, discrimination, and stability
