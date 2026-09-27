---
name: llm-fine-tuning-and-adapters
description: Decide whether fine-tuning a model is worth it and do it safely - RAG-first triage, LoRA and QLoRA basics, dataset curation and formatting, catastrophic forgetting and overfitting risks, before/after evaluation, and honest cost and time. Use when a prompt has grown enormous, when a task needs consistent style or format the model will not follow, when someone says "fine-tune", "LoRA", "QLoRA", "train a model", "PEFT", "distill", "we need a smaller model", or "should we just train it".
compatibility: Needs a GPU or a managed fine-tuning endpoint. Frameworks move fast; pin versions and read current docs before building a pipeline.
metadata:
  version: "1.0"
---

# LLM Fine-Tuning and Adapters

Fine-tuning is the *last* tool, not the first. It is expensive, slow to
iterate, and hard to roll back, and most of the problems people fine-tune to
solve have cheaper fixes.

## Triage: fix these first, in order

| Problem | Cheapest real fix | Fine-tune only if |
|---|---|---|
| Model doesn't know our facts | RAG (`skills/ai/llm-rag-systems/SKILL.md`) | Facts change faster than you can reindex, *and* the behaviour is stable |
| Model won't follow our format | Structured outputs (`skills/ai/structured-output-and-json/SKILL.md`) | Constrained decoding is unavailable on your target model |
| Model is too verbose / wrong tone | Prompt + few-shot + eval (`skills/ai/llm-prompt-engineering/SKILL.md`) | A large prompt with examples still fails past a measured threshold |
| Task is niche but outputs are consumed by code | Structured output + validation | — |
| Latency/price too high on a simple task | Route to a small model (`skills/ai/llm-cost-and-latency-optimization/SKILL.md`) | A small model cannot do the task at any prompt |
| Task is high-volume and stable | Prompt first; distill from a large model if needed | Volume justifies it and behaviour is frozen |

**RAG before fine-tuning, always.** Knowledge is what RAG is for. Fine-tuning
bakes knowledge into weights, where it cannot be updated, audited, or
per-request scoped - and it can leak to tenants you did not intend.

**Prompt first, always.** If a 2000-token prompt with 6 examples still fails,
you have an eval set and a specific failure - that is exactly the evidence a
fine-tune needs. If you do not have that, you cannot tell whether the
fine-tune helped.

## What fine-tuning actually buys

Realistically: **behaviour**, not knowledge.

- Consistently following a format or style you could not get from a prompt.
- Domain tone, terminology, and style (medical, legal, support macros).
- Narrow task performance where a small base model is close but not quite.
- Distilling a large model's behaviour into a small one for cost/latency.

It does **not** reliably buy: fresh facts, reasoning ability, or reliability on
inputs unlike your training data. If your eval is a handful of cherry-picked
examples, a fine-tune will "work" and fail in production.

## LoRA / QLoRA in one page

- **Full fine-tuning** updates all weights. Highest quality ceiling, highest
  memory and cost, and a full model artifact per experiment. Almost never the
  right first move.
- **LoRA** trains small low-rank adapter matrices attached to selected
  attention/MLP projections. You train ~1% of the parameters, the base model
  stays frozen and shareable, adapters are small (tens of MB) and swappable, and
  you can A/B several adapters against one base. This is the default.
- **QLoRA** LoRA over a 4-bit quantised base: fits consumer GPUs, small quality
  loss. Default for local experiments.
- **Rank, alpha, target modules, LR, epochs** are the knobs that matter.
  Rank 8-64 is the common range; higher rank fits more but overfits sooner.
  Target the attention projections (q/k/v/o) and usually the MLP up/down/gate;
  more targets = more capacity = more memory.
- **Merge for serving.** LoRA is a training-time trick; production serving
  usually merges the adapter into the base weights, because an unmerged
  adapter adds latency per token. Merge, re-run evals on the merged model, and
  keep the adapter + base version recorded so you can rebuild.

Training frameworks (PEFT, TRL, Axolotl, LLaMA-Factory, Unsloth) change APIs
fast. **Check current docs; do not trust a remembered `TrainingArguments` field
name.** Pin every version in the artifact record.

## Dataset curation is 80% of the work

- **Quality over quantity.** A few hundred excellent, representative, verified
  examples beat 50k noisy ones. Duplicates and near-duplicates actively hurt.
- **Match the deployment distribution.** Include the ugly inputs: terse, ramble,
  wrong language, missing context, adversarial. Train only on clean, tidy
  inputs and the model will fail exactly where your users are worst.
- **Deduplicate and decontaminate.** Remove near-duplicates; check the eval set
  is not in the training set (this silently inflates your results and is the
  most common self-deception in fine-tuning reports).
- **Format as the deployment format** - the same system/user/assistant shape,
  the same tool-call structure, the same separators. A format mismatch between
  training and serving is a silent quality loss.
- **Include refusals and edge cases** as trained behaviour, or the model will
  happily answer cases where it should escalate
  (`skills/ai/llm-agents-and-tool-use/SKILL.md`).
- **No real PII or secrets in training data.** De-identify, and document that
  you did (`skills/ai/llm-privacy-and-safety/SKILL.md`).
- **Hold out a real eval set** - from production, not from the training pool -
  and never tune hyper-parameters against your test set.

## Risks you are accepting

- **Catastrophic forgetting.** Narrow fine-tuning can degrade general ability,
  other languages, instruction-following, and safety behaviour. Always run a
  general-capability regression alongside your task eval.
- **Overfitting.** Training loss falls, held-out flatlines, and the model has
  memorised your examples. Stop on validation, not on training loss; keep
  epoch count small (often 1-3) and checkpoint every epoch.
- **No reliable rollback.** A bad adapter is a new model version; keep the base
  and the old adapter hot-swappable and make "serve base" a one-config rollback.
- **Skew.** Training on historical decisions teaches the model to reproduce
  past decisions, including the ones that were wrong. If the task is
  judgement-heavy, this is a design flaw, not a tuning problem.
- **Safety drift.** An adapter that improves helpfulness can quietly degrade
  refusal behaviour. Re-run your refusal and injection tests.
- **Model licences and acceptable-use terms** may restrict fine-tuning,
  derivatives, or serving. Read them before you build the plan.
- **Data residency:** the fine-tuned weights can memorise and regurgitate
  training data. Assume anything in the dataset can leak.

## Evaluate before and after, properly

- **Same eval set, same harness, same decoding settings** for base, prompted,
  and fine-tuned. Any difference in temperature or prompt template confounds
  the result.
- **Compare against the *prompted* baseline, not the raw base.** A fine-tune
  that beats the raw model but loses to a good prompt has failed.
- **Report held-out task accuracy plus a general-capability regression suite**
  (general instructions, other languages, refusal, safety) - not just the task
  number.
- **Check the served artifact**: merge, quantise if you will serve quantised, and
  re-eval the *served* model. Eval the thing you ship.
- **Volume test**: does it hold up on inputs from the last week of production,
  not the curated set?
- **Cost accounting**: training compute, dataset labelling/curation time,
  iteration cycles, and inference cost of the new model. Fine-tuning is a
  multi-week project, not an afternoon.

## Honest cost and time

- Iteration is the real cost: each experiment is hours of GPU time and a day of
  human loop, and you need 5-20 of them. Budget for it.
- Data prep and labelling usually exceeds the training time.
- Managed fine-tuning endpoints reduce ops but cap dataset size, lock the base
  model, and change pricing - check current terms.
- The most common outcome of an honest fine-tune attempt is "a good prompt plus
  a smaller model got us 90% of the way". That is a fine result, not a failure.

## Gotchas

- **A fine-tune on 200 examples will fit anything.** That is memorisation, not
  learning; it shows up as a good train loss and a flat held-out score.
- **Changing the chat template between training and serving silently degrades
  quality** and produces no error.
- **Adapters are not composable by default** - two adapters for one model means
  merge, re-eval, re-deploy. Version the combination.
- **Quantise-then-eval, never assume**; quantisation can move task accuracy
  several points.
- **"We trained on our support tickets" is a privacy incident waiting to
  happen** if the tickets contain PII and consent was not explicit.
- **Fine-tuned models drift from provider updates.** A pinned base means pinned
  eval load; plan for re-baselining on a model bump.
- **Do not fine-tune to memorise what retrieval can fetch**, and do not fine-tune
  to teach facts that change weekly.

## Checklist

- [ ] RAG and prompt/structured-output fixes tried and measured first
- [ ] An eval set exists with a *prompted baseline* score recorded
- [ ] Justification is behaviour/format/style, not fresh knowledge
- [ ] Dataset is representative, deduped, decontaminated, and PII-free
- [ ] LoRA (not full FT) unless there is a stated reason
- [ ] Validation-based early stopping, epoch checkpoints kept
- [ ] Held-out eval + general-capability + refusal/safety regression run
- [ ] Served (merged/quantised) artifact re-evaluated
- [ ] Base + adapter hot-swappable; rollback is one config change
- [ ] Licence, residency, and time/GPU budget reviewed with stakeholders
