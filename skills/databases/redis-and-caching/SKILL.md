---
name: redis-and-caching
description: Add a Redis cache that survives contact with production - cache-aside and write-through, TTL and staleness trade-offs, invalidation, stampede protection, negative caching, serialization, data-structure choices, and eviction policy tuning. Use when a user asks how to cache something, when a cache is serving stale or wrong data, when Redis runs out of memory or evicts constantly, when a hot key causes a thundering herd, or when deciding whether a cache is the right layer at all. Triggers on "cache", "Redis", "cache stampede", "thundering herd", "TTL", "eviction", "OOM command not allowed", "cache invalidation", "memoize".
compatibility: Examples use Redis 7.x commands; eviction behaviour and the maxmemory policy names are stable across 6/7.
metadata:
  version: "1.0"
---

# Redis and Caching

A cache is a correctness liability you chose on purpose. The output is a
cache design with an explicit staleness budget, a stated invalidation
mechanism, and a stampede guard — not just "add Redis".

## The decision: should you cache this?

Cache when all three hold:

- The same input is read repeatedly within a short window.
- The source of truth is slower or more expensive than the cache lookup.
- Staleness is acceptable for a bounded time.

Do not cache when:

- The data changes on almost every read and nobody ever reads it twice.
- The read is already indexed and fast. A cache in front of a `SELECT 1 FROM
  t WHERE pk = $1` adds a network hop and a failure mode for nothing.
- You need read-your-writes. A cache behind the write is a correctness
  problem you will debug for a week; the answer is usually write-through plus
  invalidation, or a short TTL as the safety net.
- The "cache" is actually a system of record. Redis persistence is not a
  durability story you can bet on.

## Default pattern: cache-aside

Read path caches; write path invalidates. Everything else is a variation.

```python
def get_user(user_id):
    key = f"user:v1:{user_id}"
    raw = redis.get(key)
    if raw is not None:
        return deserialize(raw)
    value = db.query_one("SELECT * FROM users WHERE id = %s", (user_id,))
    if value is not None:
        redis.set(key, serialize(value), ex=300)   # bounded TTL, always
    return value
```

Write path:

```python
def update_user(user_id, patch):
    db.update(...)
    redis.delete(f"user:v1:{user_id}")   # invalidate, do not rewrite
    return ...
```

| Pattern | When | Cost |
|---|---|---|
| Cache-aside | The default | One extra read on miss; cache may be cold after a restart |
| Write-through | You cannot tolerate a miss, and the write path is on the critical path | Write latency includes cache write; a cache write failure must not fail the DB write |
| Write-behind / write-back | Bulk loads, or a cache that is the primary | Data loss on crash; needs a durable queue; hard to reason about |
| Read-through | A managed cache (Memcached-style) — Redis is not a managed one | Little control |

Rules that are not optional for cache-aside:

- **Always set a TTL**, even if you think invalidation covers it. The TTL is
  the backstop for a missed invalidation; without it a bug becomes permanent.
- **Never return a cached value to a request that just wrote it** without
  invalidating first, or the writer sees their own stale read.
- **Cache failures are not cache failures to the caller.** Wrap Redis in a
  timeout (single-digit milliseconds), and on error fall through to the
  database. A cache outage must degrade latency, not availability.
- **One key per logical entity, versioned in the key** (`user:v1:{id}`) so you
  can invalidate an entire class of cached objects with a namespace change
  during a bad deploy, instead of scanning and deleting keys.

## Staleness budget, stated explicitly

| Invalidation | Max staleness | Cost |
|---|---|---|
| Event-based (write deletes the key) | until the invalidation lands; unbounded if events are dropped | Requires a reliable fan-out |
| TTL only | exactly the TTL | Simple; always eventually correct |
| TTL + event-based | min(TTL, event latency) | The usual choice |
| Push on write (write-through) | near zero if the write succeeds | Write path now depends on the cache |

Pick a number and put it in the design doc. "5 minutes" is a decision; "we
will invalidate it" is not.

## Invalidation is the hard part

The options, in order of how often they actually work:

1. **Delete the key on write, in the same transaction's aftermath.** Correct
   if there is exactly one writer path. The failure mode is a second writer
   (admin tool, backfill, another service) that forgets.
2. **Versioned keys (`user:v2:{id}`)** and flip the version in config. Old
   keys expire on their own. Use this for a mass invalidation or a bad deploy,
   not as the normal mechanism.
3. **Short TTL as a safety net.** Combine with (1). This is the default.
4. **Event-driven invalidation** (CDC via a change stream, or a message
   published on write). Robust to multiple writers, but you now have an
   at-least-once consumer that must be idempotent, and you have a window
   between the DB commit and the invalidation landing.

Anti-patterns:

- **Cache-fill races.** Read (miss) → write → another writer's delete lands →
   the first writer's fill re-populates the stale value after the delete. This
  is the "deleted key comes back" bug. Mitigate with a short TTL and, if it
  matters, a versioned value you compare on read.
- **`KEYS *` or `SCAN` + delete on a production instance.** `KEYS` is O(n) and
  blocks the event loop for the duration; on a large database it will look
  like a Redis outage. Use a versioned key namespace, or `SCAN` in batches
  with `UNLINK` (non-blocking free).
- **Cache-of-a-list that is rebuilt from a full table scan.** A cache miss
  that costs more than the original query makes the miss path a denial of
  service amplifier.

## Stampede / thundering herd

When one hot key expires, every concurrent request misses at once and every
one of them runs the expensive query.

```python
def get_with_stampede_protection(key, loader, ttl=300):
    raw = redis.get(key)
    if raw is not None:
        return raw
    # one loader; others wait briefly for its result
    lock = f"lock:{key}"
    if redis.set(lock, "1", nx=True, ex=10):          # acquired
        try:
            value = loader()
            redis.set(key, value, ex=ttl)
            return value
        finally:
            redis.delete(lock)      # Lua compare-and-delete; see gotchas
    time.sleep(0.05)
    return get_with_stampede_protection(key, loader, ttl)   # bounded retries
```

Prefer a Lua script for the release so the delete only happens if you still
own the lock (a plain `DEL` can delete someone else's lock after your TTL
expired and they acquired it). `GETDEL` (Redis 6.2+) is a single atomic op if
you are taking a lock by moving a value. Simpler alternative that avoids locks
entirely: **stagger the TTL** (`ttl = base + random(0, 60)`) so a mass expiry
never lines up, and/or **never let a key expire while it is hot** by extending
the TTL on read (`ttl` refreshed on a hit). Both are weaker than a lock but
far less code.

The two failure modes to know: a **cache stampede** (one key, many misses) and
a **cache avalanche** (many keys, same expiry) — same symptom, different fix
(stampede lock vs jittered TTL / staggered warm-up after a restart).

## Cache penetration with negative caching

A request for a key that does not exist misses forever and hits the database
every time. If the misses come from an attacker guessing ids, this is a real
DoS.

```python
raw = redis.get(key)
if raw is not None:
    return None if raw == b"\x00" else deserialize(raw)   # sentinel for "missing"
```

Cache the *absence* with a short TTL (30–60 s), using a sentinel value that
cannot collide with real data. A `SETNX`-with-sentinel, or a short-TTL key with
a `__miss__` value — the point is that the "not found" result is a cacheable
value.

## Memory and eviction

When Redis hits `maxmemory`, it picks a policy and starts evicting:

| Policy | Evicts | Use when |
|---|---|---|
| `noeviction` | nothing; writes return an error | Redis is a datastore, not a cache |
| `allkeys-lru` / `allkeys-lfu` | any key | The standard cache default |
| `volatile-lru` / `volatile-lru` | only keys with a TTL | Mixed workload, but a key with no TTL is unpinnable — a common bug |
| `volatile-ttl` | the key closest to expiry | Rarely right |

Key operational points:

- **Sizing is not "as much as fits."** A full Redis starts evicting at the
  watermark; if it evicts constantly, your hit rate collapses and you have
  paid for the memory and the latency for nothing. Size so that a cold-start
  storm still fits: peak working set × a safety factor. Eviction is normal
  for a cache; *thrashing* is the failure.
- `volatile-lru` silently cannot evict a key that has no TTL, so one
  `SET key value` (no expiry) can pin memory and eventually drive every
  write to OOM. Every key in a cache should have a TTL.
- `maxmemory` and `maxmemory-policy` are server-wide; in a cluster
  (`cluster-enabled yes`) they are **per shard**, and there is no cross-shard
  eviction.

Error strings and what they mean:

| String | Means | Do |
|---|---|---|
| `OOM command not allowed when used memory > 'maxmemory'` | Write path, `noeviction`, or a single value too big | Add TTLs, raise maxmemory, or shrink the value; do not ignore |
| `OOM command not allowed` on one `SET` of a 512 MB string | Value too big for `maxmemory` | Do not cache large blobs; store an object-storage pointer |
| `MISCONF Redis is configured to save RDB snapshots but is unable to persist` | A cache with persistence on and no writable dir, or running as a replica | Fix persistence or turn it off for a pure cache |
| `LOADING Redis is loading the dataset in memory` | A big RDB/AOF restore in progress | Wait; do not point your app at it during a restore |

## Choosing a data structure

| Need | Use | Not |
|---|---|---|
| Simple value, key expiry | `GET` / `SETEX` | — |
| Counter, atomic increment | `INCR` / `INCRBY` / `DECR` | read-modify-write in the app |
| Membership test | `SADD` / `SISMEMBER` / `SMEMBERS` | serializing a list |
| Recent N, ordered | `LPUSH` + `LTRIM key 0 N-1` (a capped list) | an ever-growing list |
| Priority queue / delayed work | `ZADD` with a score that is the due time; poll with `ZRANGEBYSCORE key 0 now LIMIT 0 100` | a separate queue service, unless you need more than this |
| Rate limiter | `INCR` + `EXPIRE` | a database write per request |
| Per-field cache of one object | `HSET`/`HGET`, or a JSON string with a single key | — |
| Lock | `SET key token NX EX ttl`, released with a Lua compare-and-delete | `GETDEL` on a key that may have expired |
| Pub/sub fanout | `PUBLISH` | needing history (pub/sub is fire-and-forget) |

Capping matters: an `LPUSH` list with no `LTRIM` is a memory leak with a
familiar shape.

## Serialization

- Cache whatever you will read back. If you `json.dumps` on the way in, you
  `json.loads` on the way out. Never `eval`/`unmarshal` from cache.
- **Version the serialized form or the key.** A deploy that changes a cached
  object's shape while old keys are alive produces
  `KeyError`/`unmarshal`/type errors that only appear on cache hits, i.e. only
  in production. Key version (`user:v1:` → `user:v2:`) or a field inside the
  payload handles this without a flush.
- Compress large values (gzip) only above a size threshold; the CPU cost
  applies to every hit.
- For a complex object, a hash (`HSET`) lets you fetch and update single
  fields without a full deserialise. For a value read whole every time, one
  serialized string is simpler and usually faster.

## When the cache is the wrong layer

Reach for the database instead when:

- The read is a primary-key lookup that is already fast. Fix the index
  instead (see `postgres-query-tuning`).
- The data must never be stale. Use a read replica or a read-your-writes
  token instead of a cache.
- The cache hit rate is low. A cache with a 20% hit rate adds latency and
  failure modes for one cache read in five.
- The thing is being invalidated more often than it is read. If the write
  path deletes the key more than the read path fills it, you have built a
  distributed performance tax.
- It is a write-heavy workload. Caches do not help writes; they help reads.
  If the profile is 95% writes, the fix is batching, sharding, or a different
  store.

Watch for the "cache of a cache" and the "two layers disagreeing" failure:
if you have a CDN, an app cache, and the database, define the order of
authority and the invalidation direction once, in writing.

## Gotchas

- **`DEL` vs `UNLINK`**: `DEL` frees large values synchronously and blocks the
  event loop; `UNLINK` frees in a background thread. For big cached values
  prefer `UNLINK`.
- **Deleting keys by pattern** (`KEYS`, or `SCAN` + `DEL` in a loop) is a
  production risk. Versioned keys are the safe mass-invalidation.
- **Redis persistence is not durability.** RDB snapshots and AOF give you a
  best-effort recent state; a failover to a replica can lose acknowledged
  writes. Do not use Redis as the system of record for anything you cannot
  rebuild from Postgres.
- **`KEYS` in a Lua script or a keyspace-notification handler is O(n) and
  blocks everything**, including health checks, for the duration.
- **A single very large value blocks the event loop** on read *and* on free.
  Keep cached values small; store pointers to object storage for blobs.
- **Cache keys must include the version/namespace and the environment.** A
  shared Redis across environments will serve staging data to production if
  the key prefix is shared.
- **Setting a TTL on every key means you need a `volatile-*` policy or
  `allkeys-*` consistently**; mixing them is how "some keys never get
  evicted" happens.
- **Clock and rounding in TTL maths**: `INCR` + `EXPIRE` on a counter is two
  round trips and is not atomic — use a Lua script or a transaction, or a
  single `SET key value EX ttl NX` where applicable.

## Files

- `references/cache-patterns.md` — read it when designing a specific cache
  layer: stampede locks in Lua, rate limiter, sliding-window, session cache,
  and the invalidation-via-change-stream pattern.
