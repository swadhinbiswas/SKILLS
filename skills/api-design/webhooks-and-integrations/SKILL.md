---
name: webhooks-and-integrations
description: Build and consume webhooks and third-party API integrations safely - HMAC signature verification, idempotent receivers, retry and backoff expectations, out-of-order delivery, replay, OAuth2 flows and token storage, rate limiting with jittered backoff, circuit breakers, and deciding what belongs behind an async boundary. Use when the user says "webhook", "callback URL", "signature verification", "duplicate webhook deliveries", "retry storm", "integrate with a third-party API", "OAuth2 client credentials", "rate limit 429", "circuit breaker", or is calling an external service that is slow or flaky.
compatibility: Language-agnostic. Examples use Python 3.11+ stdlib and HMAC-SHA256, the default for most providers.
metadata:
  version: "1.0"
---

# Webhooks and Third-Party Integrations

Both halves of one problem: someone is pushing events at you that you cannot
control, or you are pulling from a service whose availability and rate limits
you do not control. The design is the same: **assume delivery is at-least-once,
possibly out of order, and possibly duplicated, and assume the remote call can
block for longer than your deadline.**

## Part 1 — Webhooks you receive

### Verify before you parse

The signature check is the only thing standing between a stranger and your
write path. Do it on the **raw request body**, before JSON parsing, before any
framework middleware that could re-encode the body — re-encoding changes
whitespace and key order and breaks HMAC.

```python
import hashlib, hmac

def verify(raw_body: bytes, header: str, secret: bytes, *, prefix: str = "sha256=") -> None:
    if not header.startswith(prefix):
        raise ValueError("unsupported signature format")
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header[len(prefix):].strip()):
        raise ValueError("signature mismatch")
```

Rules:

- **Constant-time compare.** `==` on a hex digest is a timing oracle. Use
  `hmac.compare_digest`.
- **Reject unknown secrets immediately**, before doing any work. An unknown
  `webhook id` is a `401`/`403` with a generic body, not a stack trace.
- **Replay window.** A captured valid request is a valid request forever
  otherwise. Store the provider's event/delivery id and reject duplicates —
  which you need for idempotency anyway — and additionally reject anything
  whose signed timestamp is more than ~5 minutes old if the provider sends
  one. **Do not rely on the timestamp alone:** a captured request replays
  inside the window. The id-based dedupe is the real defence.
- The signing secret is per-endpoint and rotatable. Support two valid secrets
  during a rotation window so you can swap without downtime.

### Be idempotent: deliveries are at-least-once

Providers retry until they get a 2xx, and they retry on timeouts too — so a
slow handler produces duplicates by design. **Deduplicate on the provider's
event id, not on your own content hash**, and make the dedupe record survive
the retry window (days, not minutes).

```sql
CREATE TABLE webhook_events (
  provider       text        NOT NULL,
  event_id       text        NOT NULL,
  received_at    timestamptz NOT NULL DEFAULT now(),
  processed_at   timestamptz,
  payload        jsonb       NOT NULL,
  PRIMARY KEY (provider, event_id)     -- the dedupe gate
);
```

```python
def handle(raw_body: bytes, headers, secret: bytes) -> int:
    verify(raw_body, headers["x-signature"], secret)
    event = json.loads(raw_body)
    # ON CONFLICT DO NOTHING: the INSERT is the lock. A duplicate delivery
    # affects 0 rows and we return 2xx without re-processing.
    cur = db.execute(
        "INSERT INTO webhook_events (provider, event_id, payload) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING event_id",
        (PROVIDER, event["id"], json.dumps(event)),
    )
    if cur.fetchone() is None:
        return 200          # already seen: acknowledge, do not re-apply
    enqueue(event)
    return 200              # 2xx fast; the work happens off the request
```

Acknowledge **2xx immediately after durable acceptance**, not after processing.
Providers time out long before your handler finishes, and a slow ack is a
duplicate-generating machine. `INSERT` then enqueue is the durable handoff;
enqueue failures must be visible in metrics.

### Ordering and the sync/async boundary

- **Events arrive out of order.** Never assume "created" arrives before
  "updated". Carry a monotonic per-resource version (or the provider's
  `created_at`) in the payload and drop events older than the last applied
  version for that resource.
- **Make handlers order-independent.** Upsert by version, not "append".
- **Side effects must be idempotent too.** A dedupe table protects the entry
  point; if processing retries internally, the effect (charging a card,
  sending mail) needs its own idempotency key.
- **Return 2xx for "I got it", 4xx/5xx for "I will never take it".** A
  permanent failure retried 8 times is wasted on both sides; a temporary
  failure you `2xx`'d is silently lost. Distinguish them: `4xx` for a
  malformed or unauthorised payload, `5xx`/timeout for a dependency failure.

### What you send

- **Retries with exponential backoff and jitter**, 5+ attempts over hours (a
  payment notification should still arrive tomorrow). Cap the total window and
  then stop; put the dead letters where a human can replay them.
- **A replay endpoint/tool** is not optional: providers expire signatures and
  drop events. Store every sent payload with its status and attempt count.
- **Send a version in the payload** (`"schema_version": 2`) and keep the
  consumer contract documented. You cannot change the payload shape silently.
- **Never let a failed webhook fail the user's request.** The business
  transaction commits; the event goes to an outbox in the same transaction and
  a separate process delivers it. See the transactional outbox in
  `references/outbox.md` — this is the one structural decision that makes
  webhook delivery reliable.

## Part 2 — Third-party APIs you call

### OAuth2: pick the right flow

| Flow | Use when |
|---|---|
| Authorization code + PKCE | Your app acts on behalf of a user. Default for anything with a browser or mobile client |
| Client credentials | Machine-to-machine, no user. Your service is the client |
| Resource owner password | Legacy first-party clients only. Deprecated in OAuth 2.1; do not build new flows on it |

- **Authorization code + PKCE everywhere there is a user.** Never the implicit
  flow (`response_type=token`) — it exposes tokens in the URL and browser
  history. OAuth 2.1 removes it.
- **PKCE is mandatory** for public clients and good practice for all of them.
  The verifier is a high-entropy random string; the challenge is its
  `base64url(SHA256(verifier))`. S256 only, never `plain`.
- **Validate `state`** (CSRF) and, when the provider supports it,
  **`nonce`** (replay of the authorisation response). A missing or mismatched
  `state` is a failed login, not a warning.
- **Prefer the Authorization Code flow with `redirect_uri` you register
  exactly.** A `redirect_uri` mismatch is rejected by a strict provider, which
  is a feature; do not work around it by loosening your own validation.

### Token storage

| Token | Where | Why |
|---|---|---|
| Access token | In memory (process/instance cache with a short TTL) | Not on disk, not in the browser, not in a log |
| Refresh token | Encrypted at rest in your secret store, or a server-side session | Long-lived; treat as a password |
| Browser session | `HttpOnly`, `Secure`, `SameSite=Lax` cookie, opaque id | JS cannot read it, so XSS cannot steal it |

- **Do not put a JWT in `localStorage`.** Any XSS on the origin reads it and
  there is no `HttpOnly` protection. Use a cookie-based session (server-side
  or a signed, short-lived session id).
- **Do not log `Authorization` headers or refresh tokens.** Redact in the
  HTTP client, not in the code that reads the log.
- **Refresh with a single-flight lock** per credential: a burst of concurrent
  401s must produce one refresh call, not forty, or you will trip the
  provider's rate limits and invalidate your own token.
- **Handle `invalid_grant` as terminal** — the refresh token is revoked. Do not
  retry it in a loop.
- Access tokens expire in seconds to an hour. Cache them; do not fetch one per
  request.

### Rate limiting: backoff with jitter

A `429` or a `503` is a signal to slow down, not to retry immediately. Two
cases:

- **`Retry-After` present (seconds or HTTP-date)** → obey it. It is the
  provider's actual answer.
- **No `Retry-After`** → exponential backoff with **full jitter**:
  `sleep = random.uniform(0, min(cap, base * 2**attempt))`. Full jitter, not
  equal jitter and not "base * 2^n with no randomness": un-jittered retries
  from thousands of clients synchronise into a thundering herd that keeps the
  provider down and you in a retry storm.

```python
delay = random.uniform(0, min(60.0, 0.5 * (2 ** attempt)))
```

- **Honour a provider-wide rate limit** with a client-side token bucket if
  they publish limits; otherwise the adaptive backoff is all you have.
- **Cap total retries and total elapsed time**, and make the deadline a
  *budget* shared with the caller's own timeout (see below). A retry loop that
  outlives the caller's deadline is pure load.
- **Retries are only safe for idempotent operations.** A retried `POST` that
  charged a card twice is worse than a failure. Use an idempotency key on the
  provider's side if it offers one, or make the operation naturally
  idempotent (PUT, or a `?` with a request id).

### Circuit breaker, in front of the backoff

Retries alone keep hammering a service that is already down. A circuit breaker
fails fast after a threshold of failures, so you stop consuming the provider's
remaining capacity and start failing your own requests cleanly.

States: **Closed** (normal) → **Open** (fail immediately for a cool-down
window) → **Half-open** (allow a few probes) → Closed or Open. Count only
*server-side* failures (5xx, connection errors, timeouts) as trips; a `429`
means "you are going too fast", which is a different signal — treat it as backoff
input, not as a reason to open the breaker. Configure:

- **Rolling window** (e.g. last 50 requests, or 10s of rolling counts), not a
  consecutive-failure counter. Consecutive counters never trip under a partial
  failure and reset on one success.
- **Minimum request volume** before the breaker can trip, so a burst of three
  requests at 3am does not open it.
- **Cooldown** long enough to be worth probing (tens of seconds).
- **Half-open probe count** low (1–3).
- Fallback for the open state: serve stale, default, or partial data, or return
  a clear error — never hang.

Most HTTP clients expose a breaker (for example, resilience4j, Polly,
pybreaker, or a middleware in your RPC framework). Use the library's; do not
hand-roll one inside a request path.

### Timeouts: every remote call, and a budget

- **Every remote call has a connect timeout and a read timeout.** No
  exceptions. A client with no timeout is a thread/connection leak that
  becomes an outage.
- **Set a total deadline per request and propagate it down.** If the API
  gateway gives you 3s, the call to service B gets what is left after A, not
  its own 3s. Otherwise a cascade of "each call takes 3s" produces a 9s
  request and your gateway has already timed out. This is the single most
  important timeout rule.
- **Connect timeout ≪ read timeout.** A 1s connect, 5s read is a reasonable
  default; make them match your gateway's deadline.

### Make failure survivable

- **Depend on a queue whenever the remote call does not have to be in the
  request path.** Sync if the user needs the answer now; async (enqueue + a
  status resource) if it does not. A 3-second provider call inside a checkout
  request is a coupling bug.
- **Circuit-break + degrade, don't fail the whole page.** If the
  recommendations service is down, show a default. Design the degradation
  explicitly (see `skills/architecture/resilience-patterns/SKILL.md`).
- **Bulkheads** (separate pools/queues/thread limits per dependency) stop one
  slow provider from consuming every connection you have. A dedicated HTTP
  client per dependency with its own connection pool is the cheapest version.
- **Do not cache a third party's answer without a TTL and a staleness plan**,
  and never cache an *error* response as if it were data.

## Gotchas

- **A webhook request body re-encoded by middleware breaks the signature.**
  Capture raw bytes before any body parsing, and if a framework has already
  parsed it, you may be unable to verify at all — that is a framework
  incompatibility worth knowing before you ship.
- **Returning 2xx before the work is durable loses the event.** Insert-or-dedupe
  first, enqueue second, ack last. Acking first and processing inline means
  every timeout is a duplicate.
- **Providers retry on *your* timeouts, not just your 5xx.** A handler that
  takes 30s and a provider timeout of 10s means the provider will deliver the
  same event three times. Fast ack fixes it.
- **Some providers send a "ping"/test event with no signature or a special
  id.** Handle it explicitly and do not let it fall into the normal path.
- **`Retry-After` can be an HTTP-date**, not just seconds. Parse both.
- **Full jitter beats exponential-without-jitter** in every measurement that
  matters. Deterministic backoff is a synchronised load generator.
- **A `429` is not a failure to count toward circuit-breaking** in the same
  way a `5xx` is; a provider throttling you is signalling backoff, not death.
- **Refresh-token rotation means a single-flight refresh.** Ten concurrent 401s
  doing ten refreshes is how a valid refresh token gets revoked as a replay.
- **The implicit flow and the ROPC flow are the two OAuth mistakes still being
  copied from 2014 tutorials.** Use code + PKCE.
- **Do not trust a webhook's declared URL scheme/host from the payload** if
  your handler later follows links in it — that is SSRF via webhook (see
  `skills/security/web-app-security-basics/SKILL.md`).
- **Log the provider's delivery id, not the whole payload**, at `info`. Payloads
  routinely contain PII and end up in log aggregators forever.

## Safety notes

- Before raising a retry count, a rate-limit, or a breaker threshold against a
  third party, check their published terms — aggressive retrying that looks
  like abuse can get your key throttled or revoked.
- Never log or forward an access token, refresh token, or `Authorization`
  header. Redact in the HTTP client's logging layer.
- Rotating a webhook signing secret is a two-secret window (accept both, emit
  with the new one, then drop the old). Do not swap it in one step unless you
  can tolerate a missed delivery.
- Do not auto-replay a dead-lettered webhook to a third party without a human
  checking it: the payload may be months old and the far side may have changed.
