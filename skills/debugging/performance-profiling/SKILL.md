---
name: performance-profiling
description: Profile before optimising - choose the right profile type (CPU, allocation, wall-clock/contention, I/O), read flame graphs and pprof output correctly, and avoid the benchmark traps (missing warmup, dead-code elimination, GC, timer overhead) that make microbenchmarks lie. Use when something is slow, when p95 latency jumped, when a user says it got slower, or when a flame graph is pasted and needs interpreting. Triggers on "slow", "latency", "profiling", "flame graph", "pprof", "perf", "CPU bound", "why is it slow", "benchmark", "throughput", "hot path", "optimize".
compatibility: Examples use pprof/perf/valgrind conventions and a generic benchmark harness; verify the exact flags with your tool's --help and the version installed.
metadata:
  version: "1.0"
---

# Performance Profiling

Optimisation without a profile is guessing with a build step. A profile
converts "it feels slow" into a ranked list of where the time actually goes,
and it takes minutes. The discipline is: **measure, change one thing, measure
again, keep only what moved the number.**

## Workflow

- [ ] 1. Decide what "slow" means numerically: a p95 latency target, a
      throughput floor, a memory ceiling
- [ ] 2. Get a baseline under conditions that match the complaint
- [ ] 3. Pick the right profile type from the symptom (table below)
- [ ] 4. Profile, and read the *aggregate* first, then drill into hot frames
- [ ] 5. Form one hypothesis from the profile, change one thing
- [ ] 6. Re-measure with the same harness; compare distributions, not points
- [ ] 7. Keep or revert on evidence

## Step 1 — Define the number before you profile

"Slow" is not a measurement. Pick one and write it down:

- **Latency:** p50 / p95 / p99 of one operation, in milliseconds, under a
  stated concurrency level.
- **Throughput:** requests or items per second, with the worker count.
- **Resource:** CPU% per request, bytes allocated per request, peak RSS.

Note that p99 matters more than the mean for a user-facing service, and that
the mean hides the tail entirely. "Average 40ms" and "p99 4s" describe very
different products.

Match the harness to the complaint. A microbenchmark of one function on one
core will never reproduce a production latency problem caused by lock
contention at 200 concurrent requests. If the report is "slow under load",
profile under load.

## Step 2 — Pick the profile type from the symptom

| Symptom | Profile | Answers |
|---|---|---|
| High CPU, low throughput, everything slow | **CPU / on-CPU** | Which code burns cycles? |
| High memory, GC pressure, sawtooth latency | **Allocation** | What is being allocated, how much, and who keeps it? |
| CPU *low* but latency high | **Wall-clock / off-CPU** | What is the thread blocked on: lock, I/O, sleep, page fault? |
| Latency spikes under concurrency | **Contention / mutex profile** | Which lock, how long held, how contended? |
| Memory grows over time and never returns | **Heap snapshot / leak analysis** | What is retained? (`memory-leak-hunting`) |
| One call is slow, others fine | **Trace across services** | Where in the chain? (`distributed-tracing-debugging`) |
| I/O bound, low CPU | **I/O / syscall profile** | Which syscalls, how many, how much data? |
| Startup or build slow | **Phase timing** | Which phase? |

The most common misdiagnosis: a service that is slow *and* shows low CPU
usage on the app. That is off-CPU time — waiting on a lock, a database, or a
downstream call. A CPU profile of a mostly-idle process shows almost nothing,
which is correct and informative. **If CPU is low, stop CPU profiling.**

## CPU profiling: sampling versus instrumented

**Sampling** (Go `runtime/pprof`, `perf record`, async-profiler, `py-spy`,
`py-spy`/`scalene` families) interrupts the program periodically and records
the stack. Cheap, works on unmodified production binaries, gives you
statistics. The default choice.

**Instrumented** (Java Flight Recorder/JFR, `valgrind --tool=callgrind`,
`-finstrument-functions`) counts or times every call. Exact counts and call
graphs, no sampling bias, but much higher overhead (10-100x) — meaning the
program's behaviour changes under the profiler. Use on a reproducer, not on
production.

Rule: **sample in production, instrument in a reproducer.** A sampling
profile with default intervals (1-10ms) on a request that takes 200ms gives
20 samples; run the request in a loop, or lower the sampling rate, to get
enough samples to be meaningful.

### Reading a flame graph

Width is time (or samples); the y axis is stack depth; the ordering of
siblings is alphabetical, **not** causal.

Read it like this:

1. **Look at the widest bar, not the tallest.** The tall thin stack is one
   call chain; the wide one is where the time is.
2. **Ignore the runtime/GC frames** (Go `runtime.*`, `GC`, `[jvm]`,
   `memcpy`, interpreter frames) until you have ruled everything else out.
   They are often a *consequence* of allocating too much, not a cause.
3. **Find the widest frame under your own code**, then read *up* from it: the
   frame above answers "who called this and how often", the frame below
   answers "what does it call into".
4. **A wide bar that is one `memcpy`/`memmove`/`json.Marshal` is a data
   volume problem**, not an algorithmic one. Fix the shape, not the loop.
5. **A frame that is wide in every path is the thing to fix.** Optimising a
   2% frame cannot matter.
6. **Compare two profiles** — before and after, or good version vs bad
   version. Differential flame graphs find the regression instantly and are
   much more useful than a single graph.

A flat, wide profile of many small frames usually means: per-item work in a
hot loop, an abstraction that allocates, or N+1 work at a layer boundary. A
few very deep stacks means: recursion, or a deep call chain you can
short-circuit.

### Reading pprof output

```sh
go tool pprof -top -nodecount=20 <binary> <profile.pb.gz>
go tool pprof -http=:8080 <binary> <profile.pb.gz>     # interactive graph
perf report -i perf.data --stdio --sort symbol
perf script -i perf.data | less                          # per-sample stacks
```

`-top` gives the ranked list (`flat` = self time, `cum` = including
callees — **read both**; a low-flat/high-cum function is a dispatcher worth
looking at). `-list <func>` shows annotated source with per-line cost, which
is where you find the one line doing 60% of the work.

**Sampling profilers misattribute leaf frames**: time in the kernel shows as
the syscall wrapper, and inlined/interpreted frames collapse. Treat the
top-of-stack line as a hint, and confirm with a targeted measurement.

## Allocation profiling

In garbage-collected languages, allocation rate drives GC time, and GC time
drives latency tails. Allocating is usually more expensive than the code that
uses the value.

Measure bytes allocated **per operation**, not total:

| Runtime | How |
|---|---|
| Go | `testing.B` with `b.ReportAllocs()`; `go tool pprof -alloc_space` / `-inuse_space` on a heap profile |
| Java | JFR `jdk.ObjectAllocationSample` / `jdk.ObjectAllocationInNewTLAB` events |
| Python | `tracemalloc` for Python-level allocations; `memray` for native allocations (tracemalloc misses them) |
| Node | `--cpu-prof` plus `node --heap-prof`; `--trace-gc` for GC events |
| .NET | `dotnet-counters`, `dotnet-trace` |

What to look for, in order of how often it is the answer:

1. **Allocation in a hot loop.** A temporary per iteration that could be
   hoisted or reused.
2. **String/JSON churn.** Building strings by concatenation, serialising the
   same structure repeatedly, `str` in a loop, marshalling to JSON to log it.
3. **Boxing and interface conversion.** Small values escaping into
   `interface{}`/`Object` allocate.
4. **Closures and iterators allocated per call.**
5. **Duplicated data** — the same large object retained by several structures
   (a retention problem; `memory-leak-hunting`).

`-alloc_space` answers "where do allocations come from"; `-inuse_space`
answers "what is alive now". They point at different bugs.

## Wall-clock and contention

When CPU is low but latency is high, the time is *off-CPU*. Measure where the
thread was not running.

- **Blocked profiling** (Go's `runtime.SetBlockProfileRate` + `blockprofile`):
  goroutine blocking on sync primitives with a stack trace.
- **Mutex/contention profiles** (Go `runtime.SetMutexProfileFraction`,
  JFR `jdk.JavaMonitorEnter` / `jdk.ThreadPark`): time holding vs time
  waiting. High hold-time means shorten the critical section; high
  wait-time with low hold-time means the lock is fine and there is too much
  concurrency on it.
- **`strace -c` / `perf trace`** for syscall time.
- **Thread dumps** (jstack, `py-spy dump`, `pprof goroutine`, SIGQUIT) taken
  repeatedly during the incident. Ten dumps over a minute show what is
  consistently blocked; one dump is a lottery.
- **`vmstat`/`pidstat`**: high `wa` (I/O wait) or high `si`/`so` (swap) is a
  system-level answer that no code profile will find.

Contention profiles need the blocking rate turned on *before* the event; they
are not collected retroactively. That is a one-line change you must make
before the problem recurs.

## Benchmarking without lying to yourself

The classic microbenchmark mistakes, each of which produces a confident wrong
answer:

| Trap | Symptom | Fix |
|---|---|---|
| **No warmup** | first iterations are slow; JIT, page faults, connection setup, first-call lazy init | run N untimed warmup iterations, then measure |
| **Dead-code elimination** | the compiler removes the code you think you measured | consume the result (`_ = result`, `blackhole(result)`); make the value escape |
| **GC during measurement** | sawtooth latency, huge variance | force or account for GC; report allocations per op |
| **Constant folding / hoisting** | the benchmark measures nothing | vary the input so the compiler cannot precompute |
| **Unrealistic inputs** | empty arrays, single-digit sizes, all-identical data | benchmark at production sizes and shapes |
| **Different conditions** | run-to-run noise, turbo, other load, thermal throttling | pin CPU frequency, close other work, run many times, report median/percentiles |
| **Timer overhead in the measurement loop** | for nanosecond operations, the clock dominates | measure a batch and divide, rather than timing one call |
| **Not measuring what ships** | the build flag differs from production (`-O0` vs `-O2`, debug build, no JIT tier) | profile the same artefact you deploy |

Benchmark harness rules:

- **Many iterations, report a distribution.** Minimum, median, and a
  percentile — not a single number.
- **Report allocations per operation** alongside time. A 2x speedup that
  allocates 10x is usually a latency regression in disguise.
- **Run the benchmark against a realistic system**: warm caches *and* cold
  caches, with the real concurrency, over enough iterations for steady state.
- **Compare like with like.** Same machine, same input, same flags, same
  process state. A comparison across two runs a day apart on a laptop is not
  data.

A rigorous result: run the benchmark N times (e.g. 10), take the median of
medians, and report the spread. If the distributions overlap, the change made
no difference.

## What optimisation is actually worth doing

Ranked by real-world return, roughly:

1. **Algorithmic complexity** — O(n²) → O(n log n), N+1 queries batched, a
   full scan replaced by an index. This dominates everything else.
2. **I/O and round trips** — fewer, larger requests; batching; caching;
   eliminating chatty calls. Network latency usually beats CPU.
3. **Data volume** — serialise less, transfer less, send columns not rows.
4. **Lock contention and queueing** — shorter critical sections, more
   parallelism, better batching.
5. **Allocation churn** — helps latency tails via GC more than it helps
   throughput.
6. **Micro-optimisation of hot loops** — worth it only when the profile says
   that loop *is* the program.

Anything below the top of the profile is not worth the risk. "It is faster"
with no measurement of the whole operation is not an optimisation; it is a
different program.

Optimisation also has costs: a cache needs invalidation logic (itself a bug
source), a pool needs correct return-on-every-path, an inlined fast path
duplicates logic and drifts. Price those in.

## Gotchas

- **Profiling changes what you measure.** Instrumentation overhead can
  change the outcome. Never report a profiled number as a performance result.
- **Sampling profiles under-sample short functions.** A function that takes
  1ms in a 10-second profile gets 0 or 1 samples. Aggregate it into a loop,
  or the profile will hide the very code you are looking for.
- **The top of a flame graph is not always the cause.** A wide `GC` or
  `syscall` frame is usually a symptom of allocating or blocking too much.
- **The build you profiled is not the build you ship.** Profilers often
  require disabling inlining or adding debug symbols; re-check that the
  optimised build has the same shape.
- **Inlining hides small functions.** A profile may attribute work to its
  caller. Use the runtime's inlining report before concluding a caller is the
  culprit.
- **Async/await and coroutines distort call stacks.** A profile shows the
  synchronous stack at a resume point, not the logical call chain. Use
  async-aware profiling or trace the logical operation separately.
- **A "slow" system that is CPU-idle on every machine is usually waiting on
  something external.** Check the dependency, the network, the lock, and the
  disk before optimising the application.
- **Caching the first call is not optimising.** Measure steady state and cold
  start separately; they have different fixes.
- **`time` on a script measures process startup too.** For in-process
  measurement use a proper harness.
- **Percentiles need enough samples.** p99 from 20 requests is the maximum,
  not a percentile. Collect thousands for a meaningful tail.
- **Never optimise and deploy in one step during an incident.** Change one
  thing, verify the number moved, then decide. See
  `production-incident-response`.
- **The optimisation is done when it is measured, documented, and tested.**
  Keep a comment recording the before/after numbers next to the change, or the
  next person will "clean it up" and nobody will notice the regression.

## Handoff template

```markdown
Profile:   <tool, e.g. CPU sampling 100Hz, 3 min, under 20 concurrent clients>
Workload:  <command, input size, build flags, environment>
Baseline:  p50 42ms  p95 310ms  p99 1.9s   CPU 68%   1.2 MB/op
Finding:   <the 1-3 frames or call sites that account for the time,
            with their share: "json.Marshal in buildResponse 31% cum">
Change:    <one change, with the file:line>
After:     p50 41ms  p95 120ms  p99 180ms   CPU 31%   0.1 MB/op
Verdict:   keep — p99 10x better, no correctness change, covered by <test>
```

Without the "Workload" and "Baseline" lines, the finding is not reproducible
and nobody can tell whether a later change regressed it.
