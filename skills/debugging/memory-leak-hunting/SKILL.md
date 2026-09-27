---
name: memory-leak-hunting
description: Find and fix memory that is never released - managed heap growth (heap dumps, dominator trees, retainer paths) and native/embedded leaks (valgrind massif, LeakSanitizer, --leak-check) - and recognise the common leak shapes: unclosed resources, listener and timer accumulation, unbounded caches, closures capturing contexts, and connection-pool exhaustion. Use when RSS grows over time, when a process is OOMKilled, when memory climbs every request, or when a long-running service restarts on a schedule. Triggers on "memory leak", "OOM", "out of memory", "OOMKilled", "memory keeps growing", "heap dump", "retained memory", "RSS climbing", "valgrind", "leak check", "unbounded cache".
compatibility: Examples use jcmd/heap dumps, Go pprof, valgrind, and LSan conventions. Tool availability and flag spellings vary; verify with --help.
metadata:
  version: "1.0"
---

# Memory Leak Hunting

A leak is memory that is still reachable (or still allocated) after it should
have died. "The process uses a lot of memory" is not a leak — a large
legitimate working set, a cache at its configured size, and a leak look
identical in a `top` output. The difference is whether it comes back.

**The diagnostic question is always: does memory return to baseline after the
load stops?** If it does not, it is retained. If it returns, it is
fragmentation or cache sizing.

## Workflow

- [ ] 1. Confirm it is a leak, not a working set: growth over time vs peak
      load, and does it plateau
- [ ] 2. Choose managed or native; the tools and the fixes are different
- [ ] 3. Get a heap snapshot at a *known* point in the workload
- [ ] 4. Compare two snapshots; look at what grew, then at retainers
- [ ] 5. Find the retaining path, not just the object type
- [ ] 6. Fix the retention, not the symptom (calling a GC has no effect on a
      leak)
- [ ] 7. Verify with the same measurement, over a longer run

## Step 1 — Prove it is a leak

Do this before any tooling. Most "leaks" are not.

- **Steady state or growth?** A process that plateaus at 2GB under constant
  load is not leaking. A process that climbs 100MB/hour and never plateaus is.
- **Under constant load or after it stops?** Memory that returns to baseline
  when load stops is a working set, a cache, or allocator fragmentation.
- **One request, or all of them?** Reproduce with a single request in a loop
  and watch whether allocation per iteration is constant. Constant allocation
  with growing RSS means retention; growing allocation means a bug in the
  request path.
- **Which number?** RSS includes the allocator's free-but-unreturned memory
  and shared pages. In-use heap (`heap_inuse`, `HeapUsed`, `heapSize -
  free`) is the honest signal for GC languages; RSS is the honest signal for
  native.

```sh
# a loop that makes retention obvious
for i in $(seq 1 200); do
  ./reproduce-one-request.sh
  printf '%s ' "$i"
  # runtime-appropriate "current heap" number, every 10 iterations
  [ $((i % 10)) -eq 0 ] && ./report-heap.sh
  echo
done
```

If heap climbs linearly with iterations and never flattens, you have a leak
and the slope tells you the bytes leaked per request. That number is the
acceptance criterion for the fix.

## Step 2 — Managed vs native

| | Managed (GC) | Native / embedded / FFI |
|---|---|---|
| Symptom | `heapUsed` or `HeapUsed` climbs | RSS climbs, or `valgrind` reports "definitely lost" |
| Diagnostic | heap snapshot, dominator tree, retainer path | valgrind (memcheck, massif, DHAT), LeakSanitizer, heaptrack |
| Cause | a reachable reference nobody expects | `malloc` without `free`, a missing `close`/`release`, a dangling pointer |
| Fix | break the reference | call the release, fix the ownership, use RAII / `with` / a context manager |

Mixed cases are the hard ones: a managed runtime's *native* memory (buffers,
native libraries, `cgo`/FFI calls) is invisible to a heap dump, and it is
frequently the real leak. If `heapUsed` is flat but RSS climbs, the leak is
native. Conversely, if RSS is flat and `heapUsed` climbs, you have
allocation churn (see `performance-profiling`), not a leak.

## Step 3 — Managed: heap snapshots and the retainer path

### Take snapshots at two points

A single snapshot tells you what is *big*. Two snapshots tell you what is
*growing*, which is what a leak is. Take:

- **Snapshot A** after a warmup (let caches populate, JIT settle).
- Run N more iterations of the exact workload.
- **Snapshot B**.
- Force a GC before *both* snapshots, so you are diffing live data, not
  uncollected garbage.

Tools: `jcmd <pid> GC.heap_dump`, `jmap -dump:live,format=b,file=heap.hprof`,
Go's `runtime/pprof` `WriteHeapProfile` (and `runtime.GC()` first),
`node --heap-prof` / Chrome DevTools heap snapshots, Python `tracemalloc`
snapshots, `dotnet-dump collect` + `dotnet-gcdump`.

### Read them in this order

1. **Compare A and B by class.** The class whose *retained size* grew most is
   your suspect. Retained size, not shallow size: a million small strings
   retained by one map is a million retained bytes, and its own shallow size
   is irrelevant.
2. **Dominator tree.** For a given object, the dominator tree shows the chain
   of objects whose retention is responsible. The path from a GC root to your
   object is the **retainer path** — that is the bug, expressed as a data
   structure.
3. **Find the retaining reference.** Work *up* from the leaking object. Ask:
   what field of what object points here? A `Map` holding a `List` holding a
   `Listener` holding a `Context` is a leak until you break one link.
4. **Look for the ownership mistake.** The retainer path almost always ends
   in one of a small set of shapes (below).

**The retainer path is the answer.** "50,000 instances of `Buffer`" is a
symptom; "`Buffer` is retained by `RequestContext.cache`, which is held by a
`static` map keyed by session id that is never evicted" is a fix.

### Language-specific commands

```sh
# Java
jcmd <pid> GC.heap_info
jcmd <pid> GC.heap_dump /tmp/heap.hprof
jcmd <pid> Thread.print                 # deadlock check while you are here
# analyse in Eclipse MAT or VisualVM; CLI: jhat is removed in modern JDKs

# Go: heap profile is sampled, not a snapshot — read it as an allocation site list
curl -s localhost:6060/debug/pprof/heap > heap.out
go tool pprof -inuse_space -top http.out heap.out
go tool pprof -inuse_objects -top http.out heap.out
```

Go's heap profile is **sampled** (one sample per ~512KB allocated by default),
so small objects vanish. Set `runtime.MemProfileRate = 1` before the run if
you need exact small-object counts — it costs throughput, so do it on a
reproducer. `-inuse_space` = what is alive now, `-alloc_space` = what has ever
been allocated; the first finds retention, the second finds churn.

```python
# Python: tracemalloc is Python-object allocations only; memray for native
import tracemalloc
tracemalloc.start(25)
# ... run the workload ...
snap = tracemalloc.take_snapshot()          # snapshot A
# ... run the workload again ...
tracemalloc.take_snapshot().compare_to(snap, "lineno")   # what grew
```

`tracemalloc` will tell you "nothing is leaking" for a leak in a C extension.
That is a false negative caused by scope, not evidence of absence.

```sh
# Node
node --inspect ./app.js            # then Chrome DevTools -> Memory -> Heap snapshot
node --heap-prof --heap-prof-interval=131072 ./app.js
node --trace-gc ./app.js           # GC events, to see churn
```

## Step 4 — Native: valgrind and leak checkers

```sh
# Build with symbols: -g, and keep the frame pointers (-fno-omit-frame-pointer)
valgrind --leak-check=full --show-leak-kinds=all \
         --track-origins=yes --num-callers=30 ./app 2> valgrind.out
```

| `definitely lost` | Allocated, no pointer to it anywhere. Almost always a real bug. |
| `indirectly lost` | Reachable only from a `definitely lost` block. Fix the root. |
| `possibly lost` | Reachable only from an interior pointer — a common false positive from custom allocators, `realloc`, or arrays. Look, but be sceptical. |
| `still reachable` | The library or runtime is holding it. Usually not a leak. |

For *growth* rather than a one-shot leak, the tools that show the trend are
better:

```sh
valgrind --tool=massif --massif-out-file=massif.out ./app   # heap over time, snapshots
valgrind --tool=dhat --dhat-out-file=dhat.out ./app        # allocation-site history
ms_print massif.out        # interactive viewer for massif
```

`massif` gives you a peak-usage graph with a detailed snapshot at the peak;
`dhat` tells you which allocation sites grew. Both are slow (10-50x) — run
them on a reproducer, not on production.

Faster, and often enough:

```sh
# AddressSanitizer + LeakSanitizer
clang -fsanitize=address -g -o app app.c
ASAN_OPTIONS=detect_leaks=1:allocator_may_return_null=1 ./app
```

LSan runs at exit and prints a stack trace per leak — the fastest feedback
loop in C/C++. It only reports at process exit, so for a long-running service
either exit or use a periodic leak-check facility in your build.

For embedded/C++: valgrind, `heaptrack`, or your platform's tooling.
For Go with cgo: `GODEBUG=cgocheck=2` (build-time check) does not detect
leaks, but the Go heap profile's `-inuse_space` plus an RSS comparison will
show that the leak is on the native side.

## Step 5 — The common leak shapes

Recognising the shape in the retainer path gets you to the fix directly.

### Unclosed resources

File handles, sockets, database connections, subprocess pipes. Every language
has a scoped form; using it is the whole fix.

```python
with open(path) as f: ...            # Python
# Go: defer f.Close() immediately after the error check
defer resp.Body.Close()              // http response bodies leak without this
```

`defer resp.Body.Close()` is the single most common real-world Go resource
leak. `go vet` and `bodyclose` catch it; run the linter in CI.

An unclosed file descriptor shows as `lsof` output growth and eventually
`too many open files`. An unclosed connection shows as pool exhaustion
(below). On the native side, unclosed shows up as `definitely lost`.

### Event listeners, observers, and timers

The most common leak in event-driven code: a handler is registered, the
emitter is long-lived, and the closure captures the request context. Every
registration needs a matching deregistration, and the registration should
not capture more than it needs.

```javascript
// leak: the emitter lives forever, so every subscription is retained
emitter.on("message", (msg) => handleRequest(request, msg));
// fix: deregister, and capture only the minimum
const onMessage = (msg) => handle(msg);
emitter.on("message", onMessage);
// ... and on teardown:
emitter.off("message", onMessage);
```

The same shape appears as an un-unsubscribed `setInterval`, a `setTimeout`
that is cleared nowhere, a signal handler in Python's `asyncio`, and a
`Process` that is never `join`ed. Long-lived emitters (`EventEmitter`,
`ApplicationContext`, static registries, DI containers, a `self`-referential
class with instance listeners) are where these accumulate.

### Unbounded caches

A cache with no eviction policy is a leak with extra steps: it looks like
intentional performance, and it takes days to bite. `lru_cache` with a
`maxsize`, an LRU with a bound, or a TTL — one of the three, always.

The subtler version: a cache **keyed by something unbounded** (a full URL, a
user id in a multi-tenant system, a query string) where the number of keys
grows without limit. `functools.lru_cache` on a function taking a
`request` object is a classic.

Also check: negative caching that never expires, a "seen ids" set used to
deduplicate that is only read, and a metrics registry that creates a new time
series per request (a real and expensive leak in Prometheus-style systems).

### Closures capturing contexts

A closure keeps its entire enclosing scope alive. In a loop or a
per-request handler, that is the whole request object graph — including
buffers, sessions, and other large things.

```javascript
// retains the entire request, connection, and context for the emitter's lifetime
setInterval(() => { if (inflightCount > 0) scheduleRetry(request); }, 1000);
```

The fix is to extract the minimum into a small object before capturing, and
to clear the timer. Audit for: long-lived objects holding closures that
capture a `this` with many fields; callbacks stored in a queue that never
drains.

### Connection pool exhaustion

Looks like a leak, is usually one. Every symptom is a resource that is never
returned to the pool: no `finally`/`defer` around release, an exception path,
a transaction that is not committed *or* rolled back, or a client object
created per request instead of shared.

```
FATAL: sorry, too many clients already
pool timeout: timeout exceeded when trying to acquire a connection
```

Distinguish exhaustion from a leak: exhaustion recovers as soon as
in-flight work completes; a leak does not. Check the pool's `in_use` vs
`idle` count over time. Then check whether the *creation* site is per-request.

### Other shapes worth recognising

- **Thread-local storage** (`ThreadLocal` in Java, `threading.local` in
  Python, `goroutine`/`pprof` labels) that is set per request and never
  cleared. In a thread pool, the thread is reused forever, so the value is
  retained forever.
- **Interned strings / symbol tables** — interning a value derived from user
  input (a URL, a token, a full stack trace) is an unbounded intern leak.
- **Statics/globals** holding anything request-scoped, including a "temporary"
  cache added for debugging and never removed.
- **Finalizer-dependent cleanup**: relying on GC to close a resource, in any
  language. Finalizers are not deterministic and many runtimes never run them.
- **Slices/maps that only grow** (`append` without ever truncating) — a
  buffer that keeps every line ever read.
- **Class-loader leaks** in app servers: a static reference from a
  long-lived class to a class loaded by a redeployable loader keeps the whole
  loader — and every class it loaded — alive per deployment.
- **Observability that leaks**: recording a span/event/trace with a
  reference to a large context; an unclosed `Span`; a metrics label
  containing a unique id.

## Gotchas

- **Calling GC does not fix a leak, and running GC before every measurement
  makes the measurement correct.** Two different things. If forced GC does not
  reduce the number, the object is reachable — that is a retention problem,
  which is what the retainer path is for.
- **Heap snapshots are huge and take a pause.** For a service with a 4GB
  heap, expect tens of seconds of pause and a multi-gigabyte file. Do it
  during a controlled window, not during an incident.
- **`still reachable` and `possibly lost` are usually not bugs.** Fixing them
  is often a fight with the runtime's own allocator. Chasing them is a good
  way to spend a week and introduce a use-after-free.
- **A snapshot taken at the wrong moment shows the wrong thing.** Taken during
  a burst of in-flight work, it shows the burst. Take it at a quiescent point,
  after a forced GC, with the same workload state in both.
- **Sampling profilers miss small objects.** If the object count is small but
  each object is large, a sampled heap profile may be useless; use a real
  snapshot.
- **`--leak-check=full` on a long-running service never exits**, so LSan
  reports nothing. Use massif/dhat for the trend, or make the service exit.
- **Memory growth that is fragmentation looks like a leak in RSS.** Rising RSS
  with a flat in-use heap is often the allocator holding freed arenas. This
  is a real problem for memory limits but is not fixed by finding a
  reference. Look at `heap_inuse` vs `heapIdle`/`heapReleased` (Go) or
  `HeapUsed` vs `HeapCommitted` (Java) to tell them apart.
- **A "leak" in a container limit is often just a limit set too low.** If the
  working set is genuinely 1.5GB and the limit is 1GB, no amount of leak
  hunting helps. Establish the real working set first.
- **Killing and restarting a leaking service is mitigation, not a fix.** It
  is the correct emergency action (`production-incident-response`) and must
  still be paired with a real fix and a memory limit.
- **Deleting your way to a fix**: `runtime.GC()` in a request handler, a
  `System.gc()` on a schedule, or a periodic restart hides the leak and costs
  latency. It is a labelled temporary measure with a tracking issue, never a
  resolution.
- **Retainer paths through legitimate structures are still the answer.** A
  long-lived object retaining a lot is only a bug if the *amount* is
  unbounded. Distinguish "large but bounded" from "grows forever" by
  watching the slope over a long run.

## Verification

The fix is verified by the same measurement that found the leak, run for at
least as long as the original run plus a margin:

- [ ] Heap/RSS flat over the same workload for a longer period
- [ ] The slope is zero — not merely slower
- [ ] Alloc per operation is unchanged or lower
- [ ] The specific retaining structure in the retainer path is gone
- [ ] For native: `definitely lost` is zero (or down to the library's
      baseline), and `indirectly lost` with it
- [ ] A regression guard exists: a test that runs the operation N times and
      asserts heap growth is bounded — this is the only thing that stops it
      coming back

```python
def test_no_growth_over_many_iterations():
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    for _ in range(1000):
        do_the_operation()
    gc.collect()
    after = tracemalloc.get_traced_memory()[0]
    assert after - baseline < 1 * 1024 * 1024   # 1MB over 1000 iterations
```

Assert on a *bound*, not on zero: real programs have some slack, and a test
that asserts exact equality fails on an unrelated change.
