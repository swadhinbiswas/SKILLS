---
name: resilience-patterns
description: Design a system that degrades instead of collapsing - timeouts on every remote call with a propagated deadline budget, retries with exponential backoff and full jitter, circuit breakers, bulkheads, hedged requests, queues as shock absorbers, and graceful degradation. Use when the user says "resilience", "retry", "backoff", "timeout", "circuit breaker", "bulkhead", "thundering herd", "retry storm", "cascading failure", "the site went down when X was slow", or is reviewing whether a dependency can take the system down.
compatibility: Language-agnostic; examples are Python 3.11+ stdlib and generic config.
metadata:
  version: "1.0"
---

# Resilience Patterns

The goal is not "never fail". It is that a failure of any single component is
**contained**, **visible**, and **does not consume the capacity needed to
recover**. Every pattern here is either a limiter (timeouts, backoff, breakers,
bulkheads) or a buffer (queues, graceful degradation). Patterns that are not
limiters or buffers are decoration.

## Order of operations

Apply in this order. Each one is cheaper and less risky than the next, and
adding retries before timeouts is the single most common sequencing mistake —
it multiplies load on a system that is already failing.

1. **Timeout** on the call (always, first).
2. **Deadline propagation** so the whole request has a budget.
3. **Bulkhead** so this dependency cannot consume everything.
4. **Circuit breaker** so you stop calling something that is already down.
5. **Retry with backoff + full jitter**, bounded by the remaining budget.
6. **Graceful degradation** so a failed dependency is a worse answer, not an
   error.
7. **Queue** if the work did not need to be in the request path.

## Timeouts: the foundation

- **Every remote call has a connect timeout and a read timeout.** A client
  without a timeout is a thread/connection leak that becomes an outage. No
  exceptions, including calls to your own internal services and your database
  driver (`statement_timeout` in Postgres, socket timeouts everywhere).
- **Connect timeout ≪ read timeout.** 1s connect, 5s read is a sane default.
  A 30s connect timeout against a black-holed packet means every request hangs
  for 30s and the pool is exhausted in seconds.
- **Propagate a deadline, not a timeout.** The caller's remaining budget is the
  input to the next call's timeout:

```python
import time

class Deadline:
    def __init__(self, budget_s: float):
        self.expires_at = time.monotonic() + budget_s

    def remaining(self) -> float:
        return self.expires_at - time.monotonic()

    def slice(self, cap: float) -> float:
        """Timeout for the next call: our own cap, never more than what's left."""
        left = self.remaining()
        if left <= 0:
            raise TimeoutError("deadline exceeded before call")
        return min(cap, left)
```

Without this, three sequential 5s calls make a 15s request while the gateway
gave you 3s. The gateway has already returned `504` and the work continues
anyway, consuming capacity for a response nobody will read. **A retry loop
that outlives the caller's deadline is pure load** — bound total retries *and*
total elapsed time by the remaining budget, not by a count.

- **Fail fast on the cheapest thing first.** Validate locally, then a cache
  lookup, then a remote call. Do not make a network call to discover the input
  is invalid.

## Retries: backoff with full jitter

Retry only what is safe and worth retrying.

**Retryable:** connection errors, timeouts, `429`, `502`/`503`/`504`.
**Not retryable:** `400`, `401` (until refreshed), `403`, `404`, a validation
failure, a conflict that will not resolve itself. Retrying a `400` is how a
bad client takes down a service.

**Idempotency first.** A retry is only safe for an operation that is
idempotent. `GET`, `PUT`, `DELETE` are. `POST` is not, unless the remote end
honours an idempotency key (see
`skills/api-design/webhooks-and-integrations/SKILL.md`). **Never make a POST
retryable by giving it a new key per attempt** — that guarantees a duplicate.

**Full jitter.** `sleep = random.uniform(0, min(cap, base * 2 ** attempt))`.

```python
import random, time

def backoff(attempt: int, *, base: float = 0.1, cap: float = 30.0) -> float:
    """Full jitter (AWS 'Exponential Backoff and Jitter')."""
    return random.uniform(0.0, min(cap, base * (2 ** attempt)))
```

Why *full* jitter and not "exponential with a small cap on randomness":
deterministic `base * 2^n` makes every client that failed at the same moment
retry at the same moment, forever. The herd never desynchronises, so the
dependency stays down. Full jitter (uniform over the whole window) is the
variant that measured best for spreading load; equal jitter is the conservative
middle ground if you need a floor. Pick full jitter, cap the window, and state
both numbers.

- **Honour `Retry-After`** on `429`/`503` when present. It is the server's
  actual answer; your own backoff is a guess.
- **Cap attempts (2–3 for a user-facing request) and total elapsed time.** Most
  requests should not retry more than twice: the user has already waited, and a
  retried slow call delays the error they need to see.
- **Retry the whole operation, or a sub-operation, at one layer only.** Retry
  loops nested three deep multiply: 3 × 3 × 3 = 27 calls for one user action.
  Pick the layer closest to the failure and do not stack.

## The retry storm (the danger)

The failure mode that turns a dependency's brief blip into a multi-hour outage
for everyone:

```
dependency is briefly slow
  -> clients time out and retry immediately
    -> the dependency gets MORE load, now from timed-out requests that are
       still running server-side
      -> it gets slower
        -> more timeouts, more retries, at the same instant
          -> the dependency is now down because of your retries, not despite them
```

A timed-out request is **not a cancelled request**. The server keeps working on
it. Retries stack on top of work already in flight, so effective load
multiplies while your success rate falls. Every load-based auto-scaler in the
path is now scaling on a signal (in-flight requests, queue depth) that is
inflated by the retry storm.

Defences, in order of how much they matter:

- **A retry budget.** Cap retries as a fraction of total requests (the
  "retry budget" pattern: at most ~10–20% of requests may be retries, and
  retries are shed before new work once the budget is spent). This bounds the
  amplification factor at all times, which is the whole point.
- **Jitter.** Mandatory. Without it, retries are a synchronised second wave.
- **Bounded total elapsed time** and a shared deadline, so a retry cannot
  outlive the caller's patience and add load after the response is gone.
- **Circuit breakers**, so once a dependency is clearly unhealthy you stop
  calling it entirely rather than calling it "gently".
- **Load shedding / backpressure**: reject excess requests fast (429, or a
  `503` with `Retry-After`) rather than queueing work that will time out
  anyway. A bounded queue with a full-queue reject is better than an unbounded
  queue that converts overload into memory exhaustion.
- **Adaptive concurrency limits** (a semaphore per dependency, sized from
  measured latency — Little's Law: concurrency ≈ throughput × latency). This
  is the most effective modern answer because it reacts to the actual failure
  signal rather than a counter.

## Circuit breaker

Retries alone keep calling something that is already broken. A breaker fails
fast after a threshold, giving the dependency room to recover and giving you a
clean, fast error.

- **States**: Closed (normal) → Open (fail immediately) → Half-open (allow a
  few probes) → Closed or Open.
- **Count server-side failures** in a **rolling window** (e.g. last 50 requests
  or 10s of counts), not a consecutive-failure counter. Consecutive counters
  never trip during a partial failure (where good requests interleave) and
  reset on a single success.
- **Minimum volume** before tripping, so three requests at 3am cannot open it.
- **Cooldown** long enough to be worth probing (tens of seconds), then a few
  half-open probes.
- **Do not count `429` as a failure to trip on.** It means "you are going too
  fast" — treat it as backoff input. Do not count `4xx` at all; those are the
  caller's fault and will not improve.
- **Open state must have a fallback**: stale data, a default, a partial
  response, or a fast clear error. Never hang.

Use the library's breaker (resilience4j, Polly, pybreaker, or your RPC
framework's middleware). Do not hand-roll one inside a request path — the
windowing and state machine are easy to get subtly wrong and impossible to
observe.

## Bulkhead

Isolate resource pools so one dependency cannot consume everything.

- **Separate connection pools / thread pools / semaphores per dependency.**
  This is the cheapest and most effective bulkhead: the recommendations service
  has its own pool of 20 connections, so even if it hangs, checkout still has
  its own.
- **Separate queues** per consumer class so a slow consumer does not block a
  fast one.
- **Bound queue length** and reject when full. Unbounded queues are a latency
  generator: the request is accepted, sits for minutes, and times out anyway,
  while the queue eats memory.
- **Concurrency limit per dependency** (a semaphore) is the general form. Size
  it from measured latency (Little's Law) and revisit it; a static number goes
  stale the moment the dependency's latency profile changes.

## Hedging

For a **read-only, idempotent** request that has not returned by a percentile
of the latency distribution (p95/p99 — not a fixed timeout), send a second
identical request and keep the first response that arrives.

- Only for reads. A hedged write is a duplicate write.
- Only past a delay (e.g. p99 of the normal latency), never immediately, or
  you have doubled the load for no latency gain.
- **Cap the number of hedges** (one extra request) and **cancel the loser**.
  Hedging is a latency optimisation with a load cost; two hedges is three times
  the load for a small tail improvement.
- It is a poor substitute for a cache when the data is read far more often
  than it changes.

## Graceful degradation

Decide, per feature, what happens when a dependency is unavailable. Most
features have a degraded answer and nobody wrote it down.

- **Stale is better than none**: serve the last cached value with an
  "as of" timestamp. Show the time; a stale price with a timestamp is honest, a
  stale price without one is a bug.
- **Feature off, not page error**: recommendations down → no recommendations
  section. Not a spinner that never resolves.
- **Partial response**: return what you have, with typed errors for the rest
  (the same idea as GraphQL partial data). This requires your error contract to
  support per-field failures.
- **Default/empty, clearly marked**: search down → empty results with a
  message, not an infinite spinner.
- **Never**: an error that looks like data, or a silent fallback that a user
  acts on. Degrade in a way that is visible.

Decide the degraded behaviour *before* the incident. Writing it down is the
entire task; implementing it during an incident is when you invent a fallback
that shows stale prices to a paying customer.

## Gotchas

- **Retries without a timeout make things worse.** Always timeout first.
- **A retry inside a retry multiplies.** 3 nested layers of 3 retries is 27
  upstream calls for one user request. Instrument the total attempt count per
  user-facing operation, not per layer.
- **Retrying a non-idempotent write duplicates the side effect.** An
  idempotency key per *logical* operation (not per attempt) is the only fix.
- **A timeout is not cancellation.** The downstream server keeps working. This
  is the mechanism of the retry storm; it is why a "fast fail" retry policy can
  increase total load.
- **Health checks that only check the process are useless.** Liveness =
  "is the process wedged"; readiness = "can I serve traffic" (dependencies
  included, or you get a thundering herd when the DB blips). Do not put
  liveness and readiness on the same check.
- **A breaker per dependency, not per service.** One breaker for "the payment
  service" that also covers an unrelated read is too coarse and takes out
  features that do not need the broken path.
- **Queue-based buffering only works if the consumer can keep up in
  steady state.** A queue in front of a slow consumer converts an outage into
  a backlog that becomes an outage later. Measure consumer throughput.
- **Circuit breaker + no fallback = fast 500s.** The breaker makes the failure
  cheap but it does not make it survivable; you still need the degraded path.
- **Timeouts that are too aggressive cause false failures** and trip breakers
  on a merely slow dependency. Derive them from measured latency percentiles,
  not from a round number.
- **A bulkhead that is too small turns a slow dependency into a total
  failure** (every request blocked at the semaphore). Size it from real
  latency, and shed rather than queue when full.
- **Cascading failure is usually one call chain**, not one component. Trace
  the request path and put the budget and the breaker at the top of it.

## Diagnostic: "X got slow and the whole site went down"

Work this list in order; it is the actual failure pattern in most incidents:

1. **No timeout on the X call** → threads pile up → add a timeout (fixes most
   of it).
2. **Timeout present but longer than the gateway's** → work continues after the
   user gave up → add deadline propagation.
3. **Shared connection pool** → X's slow calls hold connections other paths
   need → separate pools (bulkhead).
4. **No breaker** → every request still calls X → add a breaker with a
   fallback.
5. **Retries with no jitter** → herd → full jitter + a retry budget.
6. **No load shedding** → overload accepted, everything slow → shed excess
   with a fast 429/503.
7. **No degraded mode** → X down means the page fails → define and implement
   the fallback.

## Output template

When reviewing a path for resilience, produce:

```markdown
## Dependency inventory
| Dependency | Timeout (connect/read) | Retryable | Breaker | Bulkhead | Degraded mode |

## Budgets
Gateway deadline: Xs
  -> A: 0.8s (remaining 0.8s of 3s)
  -> B: 1.0s (remaining 0.2s of 3s)
  -> C: not called if budget exhausted

## Retry policy
Max attempts per user request: n (per dependency: n)
Backoff: full jitter, base Xs, cap Ys, total elapsed <= Zs
Idempotency: how each retried operation is made idempotent
Retry budget: <= p% of requests

## Failure modes
Dependency down: <what the user sees>
Dependency slow: <what the user sees>
Overload: <what is shed, and what is kept>
```

## Safety notes

- Raising a timeout or disabling a breaker "to get through the incident" is
  how an incident becomes an outage. Any such change is temporary, must be
  written down, and must have a named owner and a removal condition.
- Do not add retries to an operation without confirming it is idempotent or
  has an idempotency key.
- When changing a timeout, check the p99 latency first: a timeout below the
  normal p99 manufactures failures.
