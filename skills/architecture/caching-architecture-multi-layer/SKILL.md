---
name: caching-architecture-multi-layer
description: Design caching across browser, CDN, application, and database tiers - choosing read-through vs write-around vs write-through, invalidation that stays correct, stampede and dogpile protection with request coalescing and jittered TTLs, and sharding a cache by key. Use when the user says "caching", "Redis", "cache miss", "cache stampede", "thundering herd", "invalidate", "TTL", "cache penetration", "too many DB hits", or is adding or fixing a cache layer.
compatibility: Language-agnostic; examples use Python 3.11+ and generic Redis/HTTP semantics.
metadata:
  version: "1.0"
---

# Multi-Layer Caching

Caching is a correctness problem wearing a performance costume. Every cache
introduces a second copy of the truth, and every copy needs a defined lifetime
and an invalidation story. A cache with no invalidation plan is a data
corruption bug with a speed bump in front of it.

## Step 0 — Should you cache this?

Cache only when a read is (a) frequent, (b) expensive relative to the cache
lookup, and (c) tolerant of being slightly stale. Before adding Redis:

- **Fix the query first.** A slow query behind a fast cache is a slow query
  that is now also stale. Check the plan (see
  `skills/databases/postgres-query-tuning/SKILL.md`).
- **Check the hit rate you would get.** A cache keyed on a value that is
  unique per request (a user id × a filter combination) is mostly misses with
  eviction overhead.
- **Cache the aggregate, not the row.** Caching a per-row lookup in a loop
  does not remove the loop.

## The tiers

Each tier costs differently and invalidates differently. Use several.

| Tier | Latency | Scope | Invalidation | Use for |
|---|---|---|---|---|
| **Browser** | 0 | One user, one machine | `Cache-Control` TTL; reload with a version | Static assets, immutable content |
| **CDN / edge** | ~10–50ms | Global, shared | Purge by URL/surrogate key; short TTL | Static files, public HTML, public read APIs |
| **Application (in-process)** | ~µs | One instance | TTL + process-local invalidation (hard across instances) | Parsed config, hot reference data, per-request memoisation |
| **Shared cache (Redis/Memcached)** | ~0.5–2ms | All instances | TTL + explicit delete/publish | Aggregates, expensive computed reads, rate limit counters, sessions |
| **Database buffer pool** | ~µs–ms | One DB | Managed by the DB | Everything, automatically; not something you design |
| **HTTP caches (client/CDN)** | varies | Proxy | `Cache-Control`, `ETag`, `Vary` | Respect, do not fight |

The in-process tier is the one people under-use and then regret: it is the
fastest and it needs no network, but invalidating it across instances requires
a pub/sub message, and a stale in-process value is the hardest kind of stale
to debug. Use it for data that changes on a schedule you control (feature
flags, exchange rates, a country list) and stamp the version into the key so a
deploy invalidates it for free.

## Read strategies

| Strategy | How | Use when | Cost |
|---|---|---|---|
| **Read-through** | Cache miss → fetch from DB → store → return | Default. The caller does not need to know | Cache fill latency on misses; stampede risk on hot keys |
| **Cache-aside** | Caller checks cache, fetches on miss | Same as read-through, but you control the fetch logic | Same, and two code paths |
| **Write-through** | Write to cache *and* DB in the same call | Reads must never miss after a write | Write is slower; cache and DB can still diverge if the DB write fails |
| **Write-around** | Write to DB only; next read repopulates | Write-heavy or rarely-read data; avoids cache churn | First read after a write is a miss (stale read for concurrent readers) |
| **Write-behind (write-back)** | Write to cache, flush to DB asynchronously | Extreme write volume, tolerance for data loss | **Data loss on crash.** Rarely right for anything you care about |

**Default: read-through (or read-aside) + TTL.** It is the only combination
that cannot lose data, and a TTL is a correctness mechanism (it bounds staleness
even if your invalidation is buggy) as well as a performance one.

Rules:

- **Always set a TTL on every key.** A cache entry with no TTL is a permanent
  bug waiting for a deploy. Choose the TTL from how stale the data may be, not
  from how much memory you want to use.
- **Choose the TTL as the business staleness tolerance.** "Prices may be up
  to 60s stale" → 60s TTL plus a purge on update. "A user's display name" →
  5 minutes is fine. "A permission check" → do not cache it, or cache for
  seconds at most, and make sure revocation is explicit.
- **Write-through for read-after-write correctness.** If a user updates their
  profile and the next page must show it, either write-through or delete the
  key on write (a delete makes the next read a miss → fresh). Write-around
  gives you a stale read here.

## Invalidation: get this right or the cache is a liability

Pick one, per key, and be consistent:

1. **TTL only** — the default. Simple, self-healing, bounded staleness. Good
   for anything that changes slowly.
2. **Event-driven invalidation** — on write, publish `order.updated:{id}`; every
   instance's in-process cache evicts on receive. Correct, and it requires the
   pub/sub to be reliable. Best for a shared cache with an in-process tier in
   front.
3. **Versioned keys** — put a version in the key (`order:{id}:v{updated_at_epoch}`)
   and just change the version. Invalidation is a no-op; old entries expire on
   their own. **This is the most robust pattern** and it costs an extra lookup
   for the version (which you usually get from the row you just wrote). Use it
   when you cannot afford to get invalidation wrong.
4. **Explicit delete on write** — `DEL key` after a successful write. Simple,
   but it is a race: a concurrent read may repopulate the cache from the
   pre-write DB state *after* your delete (read-through interleaving), leaving
   a stale entry until the TTL. Mitigate with a short TTL, or with
   "delete then write-through".

Rules:

- **Invalidate after the write commits**, not before. Invalidating before
  means a read repopulates the cache from the old state and the invalidation
  is lost.
- **Never cache a negative result without a short TTL.** "Key not found" cached
  forever is cache penetration: an attacker requests a key that does not exist,
  it is cached as missing, and the real object can never be created behind it.
  If you cache negatives, give them a very short TTL (seconds) and re-check
  on write.
- **Do not cache an error as data.** A timeout stored under a cache key is a
  stale 500 for the TTL. Only store successful results.
- **User-scoped data must be keyed by user** and never served across users.
  `user:{user_id}:settings` and a `Vary`/auth-aware key on any HTTP cache.
  Getting this wrong is a data leak, not a performance bug.
- **Cache the aggregate, and key on every input that affects the result.** A
  cached list filtered by `status` keyed only on the user id will serve the
  wrong filter's results. Include the filter (or its fingerprint) in the key.

## Stampede and dogpile protection

**Cache stampede (dogpile):** a hot key expires, or is invalidated, and a
thousand concurrent requests all miss at once and all hit the database. One
hot object, one thundering herd, and the database is what dies.

Defences, in the order to apply them:

1. **Request coalescing (single-flight).** One thread fetches; the rest wait
   on the same promise. This is the primary fix and it is a few lines.

```python
import asyncio, random

class SingleFlight:
    """One in-flight load per key; everyone else awaits the same result."""
    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future] = {}

    async def do(self, key: str, loader):
        fut = self._inflight.get(key)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut
            async def run():
                try:
                    return await loader()
                except Exception as e:
                    fut.set_exception(e); raise
                finally:
                    self._inflight.pop(key, None)
            asyncio.create_task(run())
        return await fut
```

2. **Probabilistic early expiry (XFetch).** Recompute slightly *before* the TTL
   expires, but only on one randomly chosen requester, so refreshes spread out
   instead of aligning:

```python
if now() >= ttl - delta * random.random():   # delta = 10% of the window
    value = await loader()                  # this one request refreshes
    store(key, value, ttl=window)
else:
    return cached
```

3. **Jittered TTLs.** `ttl = base + random.uniform(0, base * 0.1)` so keys
   written together do not expire together. Cheap, and it removes most
   alignment.

4. **Soft/hard TTL.** Serve the stale value while one request refreshes in the
   background. Staleness becomes bounded by the hard TTL rather than by
   failure to refresh. This is the right answer for a page that must never
   error.

5. **Never let a cache miss be a hard dependency.** If the DB is down, serve
   stale (soft TTL) or a default, not a 500.

**Cache penetration** is different: many requests for keys that do not exist
(and never will), so the cache never helps. Defences: short-TTL negative
caching, a bloom filter for large key spaces, validating the key's shape
before lookup, and rate limiting per user. It is usually an attack; treat it as
one.

**Cache avalanche** is many keys expiring at once (a deploy, a mass update, or
aligned TTLs). Same defences, plus warming the cache before a deploy cutover.

## Sharding a large cache

- **Shard by a hash of the key** across nodes (client-side consistent hashing or
  a proxy). Avoid a single hot node: if one key is a celebrity, hashing does
  not help by itself.
- **Replicate hot keys** to several nodes and read from any (reads are cheap;
  invalidation is the hard part). Or **cache a single hot value in-process**
  in every instance with a very short TTL (seconds) plus a pub/sub invalidation.
- **Precompute and fan out**: a periodic job writes an aggregate to the cache
  (a top-100 leaderboard) rather than computing it per read. Counting and
  ranking are the operations that stampede hardest.
- **Cap per-tenant or per-user cardinality.** One user with 10,000 distinct
  filter combinations can evict the entire cache for everyone. If you cache
  per-filter results, bound the per-principal key count or use a separate
  cache/namespace per tenant.

## CDN and HTTP caching

For a public read API or static content, the CDN removes load entirely and
costs you only an invalidation problem.

```
Cache-Control: public, max-age=60, s-maxage=300, stale-while-revalidate=60
ETag: "v7"
```

- `max-age` is the browser TTL; `s-maxage` is the shared-cache (CDN) TTL.
  `private` forbids shared caches. `no-store` forbids both (only for genuinely
  sensitive data — it defeats all caching).
- `Vary: Accept-Encoding` or the CDN caches each encoding separately; a wrong
  `Vary` serves a gzipped body to a client that cannot decode it.
- `stale-while-revalidate` lets the edge serve stale while it refreshes — the
  cheapest stampede protection there is.
- **Purging**: CDNs purge by URL or a surrogate key. Tag cache keys with a
  surrogate key (`order:01J8Z`) and purge by tag on write. If you cannot purge
  a URL, use a short TTL and accept the staleness window.
- **Never CDN-cache a per-user response** without `Vary`/auth-aware keying.

## Gotchas

- **A key that is not unique per input serves the wrong answer.** Include every
  input (user, filter, sort, locale, version) in the key, or fingerprint them.
- **Invalidating before the write commits is lost invalidation.** A concurrent
  read repopulates from the old state. Invalidate after commit.
- **A read-through delete race** leaves a stale entry until the TTL.
  Write-through or versioned keys close it.
- **A cache with no TTL is a permanent bug.** There is no such thing as
  "invalidate everything on deploy" in production.
- **Negative caching without a short TTL is cache penetration**, and it also
  blocks creation of the object later.
- **Caching errors** turns a transient failure into a TTL-long outage for every
  user. Do not store non-2xx responses.
- **The in-process tier cannot be invalidated across instances** by deleting a
  shared key. Either pub/sub the invalidation, use versioned keys, or keep the
  in-process TTL very short.
- **Unbounded key growth** is a memory leak with a TTL. Set a max memory
  policy (LRU/LFU eviction) and monitor evictions: a high eviction rate means
  the cache is too small and the hit rate is collapsing.
- **A cache in front of an unindexed query** hides the problem until the cache
  misses. Fix the query.
- **Stampede protection is not optional for hot keys.** A single popular
  product page or a top-100 leaderboard is enough.
- **Caching a "current user" or a permission decision** with a long TTL is a
  privilege-escalation bug (a revoked role stays active). Cache identity
  briefly; do not cache authorisation decisions without an explicit
  revocation path.
- **`s-maxage` without `Vary`** serves one user's personalised body to
  everyone. Treat any user-scoped CDN caching as a leak until proven otherwise.

## Output template

When adding or reviewing a cache, produce:

```markdown
## Key design
Key: <prefix>:<id>:<fingerprint-of-filters>
  (list every input that changes the result; state the hash function)
TTL: <duration> (+ jitter), justified by the business staleness tolerance
Negative caching: yes/no, TTL if yes

## Strategy
Read: read-through / read-aside
Write: invalidate-after-commit / write-through / versioned key
Cross-instance invalidation: pub/sub / versioned keys / short TTL only

## Stampede protection
Single-flight: yes/no
Early expiry (XFetch): yes/no, delta
Soft/hard TTL: yes/no
TTL jitter: yes/no

## Sharding
Nodes, key distribution, hot-key replication, per-tenant key cap

## Invalidation on write
Exact event or code path that evicts/versions the key, after commit

## Metrics
hit rate, eviction rate, p99 read latency (cache vs origin), stampede count,
key count vs memory limit
```

## Safety notes

- Never add a long TTL to a value that encodes a permission, a price, a
  balance, or anything else whose staleness has a business or safety
  consequence. If it must be cached, cache it briefly and invalidate on write.
- Removing or changing a cache key format is a coordinated deploy: old and
  new keys coexist safely only if the new key includes a version and old
  entries expire. Say so before changing a key scheme.
- Do not raise a TTL to fix a low hit rate without checking the key design
  first; a wrong key with a long TTL is a correctness bug.
