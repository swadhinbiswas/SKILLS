---
name: llm-agents-and-tool-use
description: Build an LLM agent that calls tools reliably - tool schemas and descriptions that get picked correctly, the agent loop with budgets and stopping conditions, planning vs reacting, multi-step orchestration, memory and compaction, and guardrails for non-deterministic runs. Use when a task needs model-driven tool use, when the agent picks the wrong tool or loops forever, or when the user asks "should this be an agent", "function calling", "tool use", "ReAct", "multi-agent", "agent loop", "tool schema".
compatibility: Provider-agnostic; tool-calling API shapes differ by vendor. Check current docs for parameter names and parallel-tool support.
metadata:
  version: "1.0"
---

# LLM Agents and Tool Use

An agent is a **non-deterministic control loop around a model**. Use one when
the *path* to the answer cannot be written down in advance. Everything else
should be a pipeline.

## Should this be an agent? (answer first)

Build an agent only if **all four** are true:

1. The steps genuinely depend on intermediate results you cannot know ahead of
   time.
2. You can describe the success condition well enough for the model to hit it.
3. You can bound the work (steps, tokens, time, money).
4. You can tolerate an occasional wrong or partial result without user harm.

Otherwise use a **deterministic pipeline**: fixed steps, code doing the
orchestration, the model used as a component (classify, extract, draft) at each
step. A pipeline is cheaper, faster, testable, and debuggable. The honest
default for most production features is a pipeline with a model in it.

| Need | Build |
|---|---|
| Classify → route → call one API | Pipeline |
| Fetch 3 known URLs, summarise | Pipeline |
| Summarise, then rewrite if too long | Pipeline |
| "Fix this failing test suite" across an unknown repo | Agent |
| "Research this vendor across sources I have not seen" | Agent |
| Open-ended investigation, user watching and steering | Agent |

Hybrid is usually right: a pipeline with **one** agent step in the middle, so
the agent's freedom is scoped to one sub-task with a checkable output.

## Tool definitions that get used correctly

The model chooses tools from names and descriptions. That is the entire UI.

- **Name is a verb phrase, not a noun.** `search_docs`, `create_issue`,
  `refund_order`. Avoid near-duplicates: `get_user`, `fetch_user`,
  `lookup_user_account` guarantee mis-selection.
- **Description says when to use it and when not to**, in one or two sentences,
  and includes a trigger word the user would actually type. The description is
  the retrieval key for the tool.
- **Parameter names match the domain, not the implementation.** `order_id`,
  not `id`; the model has no idea your table is `orders.id`.
- **Enums, not free strings**, for closed sets. Enums are enforced by the
  schema and eliminate an entire class of hallucinated arguments.
- **Describe units and formats in the parameter description**: `timeout_ms:
  integer, total wait budget in milliseconds, cap 30000`.
- **Flat schemas.** Deeply nested required objects with 4 levels of nesting are
  where call validity collapses. Compose several small tools instead of one
  deep one.
- **Few, well-scoped tools beat many generic ones.** Beyond roughly 20-30
  exposed tools, selection accuracy drops; group them and expose per domain.
- **Mark side effects explicitly** — a tool that writes, sends, deletes, or
  spends money must say so in its description, not just in your code.
- **Return errors as data the model can act on**: `"error": "no order with id
  42; did you mean 4021?"`. A stack trace is an unhelpful turn-end.

Tool description template:

```
name: refund_order
description: Refund a paid order back to the original payment method. Use when
  the user asks for a refund, money back, or to reverse a charge. Only for
  orders in status 'paid' or 'shipped'; not valid for already-refunded orders.
  Refunds cannot be reversed by this tool — check with the user first for
  orders over $500.
params:
  order_id: string, required, format "ord_<id>"
  amount_cents: integer, optional; omit to refund the full remaining amount
  reason: enum [duplicate, damaged, wrong_item, other], required
```

## The agent loop

```
state = { messages, step, tokens_spent, deadline, scratch }
while step < max_steps and now() < deadline:
    resp = model(messages, tools=tools, tool_choice=auto)
    if resp has no tool_calls:      # terminal
        return resp.text
    for call in resp.tool_calls:
        result = dispatch(call)      # validates, executes, catches
        messages.append(tool_result(call.id, serialize(result)))
    step += 1
return "gave up" with what was accomplished
```

Non-negotiable parts:

- **`max_steps` and a wall-clock deadline**, always. An unbounded loop against a
  billed API is an outage with a credit-card attachment. Enforce both.
- **A token/cost budget** tracked across the loop, not per call.
- **Tool errors are non-fatal.** Append the error as a tool result and let the
  model recover. Aborting the whole run on one `404` wastes everything already
  done.
- **Validate every argument against the schema at the dispatch boundary**, even
  when the provider claims constrained decoding. Trust your own validation.
- **Serialise results with ids** and keep messages bounded — see memory.
- **Stop on repeated failure**: same tool + same arguments twice, or two
  consecutive no-progress steps. Model loops are real and produce infinite cost.
- **Make the final answer a terminal state.** If the model produced 12 tool
  calls and no text, that is a failure, not a result.

## Planning vs reacting

- **Reacting** (one step, look at the result, decide the next) is the default.
  It is cheap and adapts to reality.
- **Planning** (make a todo list first, then execute) helps when the task has
  5+ genuinely ordered steps and early choices constrain later ones. The plan
  must be a *model-visible artefact* you can show the user and re-plan against.
- **Planning is a hypothesis, not a contract.** Make the model re-read the plan
  after each step and revise it. A rigid plan executed without re-planning is
  worse than no plan, because it forces work the situation no longer needs.
- **Do not use hierarchical "manager agent writes sub-agent prompts"** unless
  you have evals showing single-agent fails. It multiplies cost and failure
  modes, and the sub-agent's output is unverifiable.
- **Ask for a short plan as a tool** (`create_plan(items)`) rather than in
  prose — it is parseable, displayable, and you can validate it.

## Multi-step orchestration

- **Independent calls go in parallel**, not sequentially. If the model requests
  three independent lookups in one turn, dispatch them concurrently. Sequential
  dispatch of independent calls is the most common latency bug in agent code.
- **Batching beats looping.** Prefer one `search_docs(query=[a,b,c])` over
  three calls; this is why tools should take lists where natural.
- **Prefer one multi-purpose tool over many single-purpose tools** when the
  domain is homogeneous (e.g. `get_records(table, ids=[...])`), provided it
  stays within a single trust domain and does validation.
- **Make the expensive thing the first thing** when a step may be terminal.
  Fetch, then decide — not decide, then fetch.
- **Idempotency**: if a retry can repeat a side effect, the tool must accept an
  idempotency key. Agents retry.

## Memory and compaction

- **Summarise, do not truncate.** When the message history hits a budget,
  replace old turns with a structured summary: goal, decisions made, facts
  learned, what was tried and failed, what remains. Raw `messages[:-k]` throws
  away exactly the constraints the model needed.
- **Keep scratch state structured and separate** from the transcript. Findings,
  intermediate results, and computed values belong in a named scratch area you
  render into the prompt when relevant.
- **Memory across turns is a product decision, not a technical one.** Decide
  what is stored, where, for how long, and who can read it. See
  `skills/ai/llm-privacy-and-safety/SKILL.md`.
- **Re-inject invariants on every compaction.** The "never do X" instruction
  from turn 1 is the first thing a summary drops, and the first thing that then
  gets violated.
- **Files over context for large artifacts**: write intermediate output to disk
  or a store and pass a pointer, not the content.

## Guardrails and reliability

Agents are non-deterministic by construction. Assume a wrong turn happens and
contain it:

- **Permissions at the tool, not the prompt.** A read-only tool is read-only.
  The model's belief that it "was told not to" is not a control.
- **Human confirmation for irreversible or outward-facing actions**: sends
  money, emails or messages, deletes, merges, deploys, opens PRs, changes
  permissions. Require explicit approval in the UI, with the concrete diff or
  payload shown.
- **Scope credentials to the minimum**, and scope them per tool. A shell tool
  with a repo-wide deploy token is a production incident waiting for a confused
  step.
- **Deny-list paths and commands** in a filesystem or shell tool, and log every
  tool call with its arguments. You cannot audit what you do not record.
- **Injection is a real vector through tools**: untrusted content (a web page, an
  issue body) that reaches the prompt can induce tool calls. Treat retrieved
  text as data, and require approval for anything the model was "asked" to do
  by that text.
- **Sandbox anything with side effects** — container, restricted credentials,
  egress allowlist.
- **Max side effects per run** (e.g. at most one write tool per run) turns a
  catastrophic failure into a merely annoying one.

## Evals for agents

- Replay the same task list across prompt/model changes and diff the traces.
- Score the *trace*, not just the final answer: did it call the right tools, in a
  sane order, with valid arguments, without repeats?
- Include tasks that must **fail cleanly**: unanswerable question, a tool that
  errors, a user request that should be refused. An agent that always "succeeds"
  is the dangerous one.
- Track per-task: success, steps, tool-call validity rate, cost, wall time.
  Steps and cost are leading indicators — a prompt change that doubles them for
  +1% success is a regression in practice.
- See `skills/ai/llm-evaluation/SKILL.md`.

## Gotchas

- **A tool that returns everything is worse than no tool.** Large results blow
  the context, bury the signal, and cost output tokens to re-summarise. Return
  the 5 relevant fields, plus a count and a "more available" marker.
- **`tool_choice: required` / forced tool use** is useful for one deterministic
  step, terrible for a whole loop — it prevents the model from ever answering.
- **Parallel tool support is not universal.** Check whether your provider
  returns multiple calls in one turn; if not, dispatch your own concurrency from
  a single call and label the results clearly.
- **Model-provided arguments are untrusted input.** `path`, `sql`, `url`, and
  `shell` arguments need server-side validation, allowlists, and parameterised
  queries. This is the single highest-severity bug class in agent code.
- **Tool names that are prefixes of each other** (`list` vs `list_files`) get
  mis-selected; avoid near-collisions.
- **Streaming and tool calls conflict.** You usually cannot render partial text
  and then act; decide whether the turn streams text or streams structured
  tool arguments.
- **"The model ignored the tool" is usually a description problem**, not a
  capability problem. Re-read the description as if you were the model choosing
  between 20 options.
- **Multi-agent systems are not automatically more capable.** They are more
  parallel, more expensive, and harder to evaluate. Prove you need it.

## Checklist

- [ ] A pipeline was considered and rejected for a stated reason
- [ ] Tool names/params match the domain; enums for closed sets
- [ ] Each description says when to use *and* when not to
- [ ] `max_steps`, wall-clock deadline, and token budget enforced
- [ ] Arguments validated at the dispatch boundary
- [ ] Independent calls dispatched concurrently
- [ ] Tool errors returned as recoverable data
- [ ] Irreversible actions require explicit human approval with a payload shown
- [ ] Every tool call and argument logged
- [ ] Trace-level evals exist, including must-fail-cleanly cases
