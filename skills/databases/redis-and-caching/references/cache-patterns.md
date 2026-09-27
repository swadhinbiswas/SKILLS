# Cache patterns

Concrete implementations for the cases that come up repeatedly. Redis 7.x.

## Stampede lock, atomically released

The release must be a compare-and-delete, or a slow loader whose lock has
expired will delete the *next* loader's lock.

```lua
-- release_lock.lua
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
```

Python driver sketch:

```python
import os, time, uuid

LOCK_TTL = 10        # must exceed the loader's p99, or the lock is useless
token = str(uuid.uuid4())

if redis.set(lock_key, token, nx=True, ex=LOCK_TTL):
    try:
        value = loader()
        redis.set(key, value, ex=ttl + random.randint(0, 60))  # jitter
        return value
    finally:
        redis.eval(RELEASE_LUA, 1, lock_key, token)
else:
    for _ in range(5):                 # bounded wait, then fall through
        time.sleep(0.02)
        raw = redis.get(key)
        if raw is not None:
            return raw
    return loader()                     # give up waiting; serve it yourself
```

Tune `LOCK_TTL` above the loader's worst-case duration. Too short and two
loaders run concurrently (correct but wasteful); too long and a crashed
loader blocks refreshes for the whole TTL.

## Rate limiter: sliding window counter, two keys

```lua
-- fixed-window with the previous window weighted, in Lua so it is atomic
local cur = redis.call("INCR", KEYS[1])
if cur == 1 then redis.call("PEXPIRE", KEYS[1], ARGV[1]) end
local prev = tonumber(redis.call("GET", KEYS[2]) or "0")
local elapsed = tonumber(ARGV[2]) / tonumber(ARGV[1])   -- fraction of window
local estimate = prev * (1 - elapsed) + cur
if estimate > tonumber(ARGV[3]) then
  return {0, math.ceil((estimate - tonumber(ARGV[3])))}
end
return {1, math.floor(tonumber(ARGV[3]) - estimate)}
```

Two windows, `rate:{id}:{window}` and `rate:{id}:{window-1}`, each with a
2×TTL expiry. Cheaper than a sorted set per request, and accurate enough for
abuse control.

## Sliding window log, exact

When precision matters (billing, not abuse control) use a sorted set and
trim on each request — one `ZREMRANGEBYSCORE` plus one `ZADD`, ideally in a
Lua script so they are atomic. Cost is O(log n) per request with memory
proportional to the window's request count, so bound it.

## Sliding TTL ("keep hot keys alive")

Refresh the TTL on every hit, in one round trip:

```lua
if redis.call("EXISTS", KEYS[1]) == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
  return redis.call("GET", KEYS[1])
end
return nil
```

This makes a hot key effectively immortal (it is never evicted, never
refreshed by a loader) and turns "cache miss storm after restart" into
"slow ramp". The cost: you can no longer distinguish "fresh" from "stale"
by TTL, and a permanently-hot key pins memory.

## Session cache

Cache the session object under `sess:{id}` with a TTL equal to the session
lifetime, and **delete on logout** — an un-invalidated session cache is a
security bug, not a performance one. Rotate the session id on privilege
change (login, MFA) and invalidate the old key; otherwise a fixated old
session id keeps working.

## Delayed jobs / lightweight queue

```python
# enqueue: score is the epoch seconds it becomes due
redis.zadd("jobs:due", {"payload": json.dumps(job)}, due_ts)

# claim atomically: remove and return due jobs
# ZRANGEBYSCORE then ZREM is a race; do both in Lua:
```

```lua
local jobs = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", ARGV[1], "LIMIT", 0, ARGV[2])
for i, j in ipairs(jobs) do redis.call("ZREM", KEYS[1], j) end
return jobs
```

This gives at-most-once delivery: a worker that dies after `ZREM` loses the
job. For at-least-once you need a processing list with a visibility timeout
(the `BRPOPLPUSH`/reliable-queue pattern) or a real broker. Do not call a
sorted set a durable queue.

## Invalidation via a change stream

For a cache serving a shared database with several writers:

1. Postgres: `CREATE PUBLICATION ... FOR ALL TABLES` and a logical
   replication slot; consume with Debezium or your own `pgoutput` client.
2. The consumer receives row changes and deletes the corresponding cache keys.
3. The consumer is at-least-once: it must be idempotent, and a slot left
   behind when the consumer dies **holds back WAL and can fill the disk** —
   `ERROR: replication slot "x" is active for PID ...` plus a growing
   `pg_replication_slots` `pg_wal_lsn_diff`.

Prefer this only when read-your-writes and cross-writer correctness genuinely
matter. Otherwise: invalidate in the write path, keep a short TTL, and accept
a bounded staleness window you wrote down.

## Bulk prewarm after a cold start

Never fill the cache in one loop. Warm in a `SCAN`-based, rate-limited loop
with the cache's own stampede protections irrelevant (there is no traffic
yet) and a jittered TTL so expiry never lines up:

```python
for key in scan_iter(match="user:v1:*", count=500):
    redis.set(key, load(key), ex=300 + random.randint(0, 120))
    time.sleep(0.001)
```

Do it during deploy, and expect it to take as long as the source can serve
those reads. A synchronous prewarm in a deploy hook is a common way to turn a
cache into a startup dependency.
