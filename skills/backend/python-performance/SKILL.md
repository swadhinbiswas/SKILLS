---
name: python-performance
description: Make Python measurably faster by profiling first (cProfile, py-spy, line_profiler, scalene, tracemalloc) and then fixing what the profile shows - the GIL's real limits, __slots__, hot-loop attribute lookups, array/numpy for numeric work, comprehensions vs generators, and string/bytes formatting costs. Use when something is slow, when a user says "this is slow", "too many function calls", "high memory", or when picking between a micro-optimisation and a real fix. Triggers on "cProfile", "py-spy", "line_profiler", "scalene", "GIL", "__slots__", "make it faster", "hot loop".
compatibility: Python 3.11+. py-spy, line_profiler, scalene, memray and uvloop are separate installs; tracemalloc and cProfile are stdlib.
metadata:
  version: "1.0"
---

# Python Performance

**Profile before you optimise.** Micro-optimising before measuring is wasted
effort that usually targets the wrong line and always makes the code worse to
read. The whole discipline is: get a profile, change the one thing it points
at, re-measure, keep it only if the number moved. See
`debugging/performance-profiling` for choosing a profile *type* and reading
flame graphs; this skill is Python-specific execution.

## Workflow

- [ ] 1. Write a number: p50/p95 latency or throughput, at a stated input size
- [ ] 2. Reproduce with a realistic harness (real data shape, real concurrency)
- [ ] 3. `cProfile` to find *which function*; `line_profiler` for the one line
- [ ] 4. If the process is too short to profile, attach `py-spy` to the live one
- [ ] 5. Fix the top cost only
- [ ] 6. Re-measure the same harness; keep or revert on evidence

## Step 1 — Pick the tool

| Question | Tool | Command |
|---|---|---|
| Which function costs the most? | `cProfile` (stdlib) | `python -m cProfile -s cumtime prog.py` |
| Which *line* inside it? | `line_profiler` | `kernprof -l -v prog.py`, then `python -m line_profiler prog.py.lprof` |
| What is the live process doing? | `py-spy` (no code change, no debug build) | `py-spy top --pid 1234`; `py-spy record -o flame.svg --pid 1234`; `py-spy dump --pid 1234` |
| CPU *and* memory, and where memory went native? | `scalene` | `scalene prog.py` |
| Python-level allocations, live vs churn | `tracemalloc` (stdlib) | see below |
| Native allocations too (C extensions) | `memray` | `memray run prog.py` |
| Blocking call in an async app | `py-spy dump` under load | see `python-async-patterns` |

`cProfile` and `line_profiler` are **deterministic and have high overhead** —
use them on a single-threaded reproducer, never in production.
`py-spy` uses sampling and has near-zero overhead, so it is the only one safe
to point at a live production process. That difference matters: a profile you
cannot take in production can point at the wrong bottleneck entirely.

## Step 2 — Read the cProfile output

```sh
python -m cProfile -s cumtime bench.py | head -30
```

Columns: `ncalls`, `tottime` (self time, exclusive of callees), `cumtime`
(inclusive). Read the same way as any profile:

- **High `tottime` / low `cumtime`** = a leaf that burns time itself. Usually a
  hot loop, a big allocation, or a formatting call. Optimise it.
- **Low `tottime` / high `cumtime`** = a dispatcher that mostly calls others.
  The time is in the callees; do not optimise the dispatcher.
- **High `ncalls` with small `tottime` each** = per-call overhead dominating.
  Fix by calling less: batch, cache, or inline.

```python
import cProfile, pstats, io

pr = cProfile.Profile()
pr.enable()
result = workload()
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cummax").print_stats(25)
print(s.getvalue())
```

Sorting by `cummax` (or `tottime`) surfaces the top consumer first; the default
`callers`/`callees` view buries it.

## Step 3 — Line-level with line_profiler

```python
# one-time, in the file you are profiling
@profile
def transform(rows):
    out = []
    for r in rows:
        out.append(r["a"] * r["b"])
    return out
```

```sh
kernprof -l -v bench.py
python -m line_profiler bench.py.lprof
```

Output is per-line hit count and time; the line with the most time and the
least code is your target. `line_profiler` on a function called millions of
times adds real overhead — wrap it in a flag so it is off in production.

## Step 4 — Memory with tracemalloc

`tracemalloc` sees Python-level allocations; it misses anything a C extension
allocates, which is why `memray` or `scalene` exist for the latter.

```python
import tracemalloc
tracemalloc.start(25)                      # 25 frames of traceback depth
snapshot_before = tracemalloc.take_snapshot()
# ... run the suspect code ...
snap = tracemalloc.take_snapshot()
for stat in snap.compare_to(snapshot_before, "lineno")[:10]:
    print(stat)                          # shows file:line and size delta
```

`statistics("lineno")` attributes to the line that allocated (most actionable);
`statistics("traceback")` shows the full call path (best for a leak, where the
retaining path differs from the allocating one). Growth that never returns
between snapshots is a leak, not churn — see `memory-leak-hunting`.

## The GIL: what it actually blocks

The Global Interpreter Lock means **one Python bytecode at a time per process**.
Consequences, precisely:

- **Blocked by the GIL:** CPU-bound *pure Python* threads. Ten threads doing
  integer maths run serially. `threading` is useless for parallelising Python
  bytecode.
- **Not blocked by the GIL:** anything that releases it —
  `I/O`, `time.sleep`, most C extension calls (numpy, hashlib, compression,
  `ssl`, `json` on large payloads), and `sys.setswitchinterval` yields. I/O
  and C-extension work **can** genuinely run on multiple threads.
- **Unblocked by free-threading (PEP 703, CPython 3.13+ experimental,
  3.14 onward more usable):** a no-GIL build runs Python bytecode in parallel.
  Real today only if you opt in and your dependencies are compatible; a
  C extension without free-threaded support falls back to the GIL for that
  call. Do not design around it — just know the option exists.

Practical rules:

- Parallelise CPU work with **processes** (`ProcessPoolExecutor`,
  `multiprocessing`), paying ~50-500ms spawn and pickling cost. Chunk the work
  so you pay spawn once.
- **numpy/scipy release the GIL** for their inner loops, so numpy on threads
  *is* parallel. This is the one high-value exception.
- `sys.setswitchinterval(0.0005)` trades context-switch overhead for fairness;
  it can make contention *worse*, not better. Measure, do not guess.
- Never use threads to speed up CPU work "because async/GIL will handle it".
  It will not.

## Optimisations worth making, roughly in ROI order

### 1. Call less / do less (the only ones that are usually right)

- Fix the algorithm. O(n²) → O(n log n) or O(n). This dwarfs everything
  below.
- Batch I/O: one query fetching 1000 rows, not 1000 queries.
- Cache a repeated call only if the profile shows it repeated, and give the
  cache a bound. See `architecture/caching-architecture-multi-layer`.

### 2. `__slots__`

A plain class instance keeps a `__dict__`, so every attribute access is a dict
lookup and every instance allocates a dict. `__slots__` gives a fixed layout:

```python
class Point:            # every instance carries a __dict__
    def __init__(self, x, y):
        self.x, self.y = x, y

class Point:            # no __dict__, no per-instance dict, faster attr access
    __slots__ = ("x", "y")
    def __init__(self, x, y):
        self.x, self.y = x, y
```

- Worth it for many short-lived instances (tens of thousands+) or as a plain
  memory win. Typical: faster attribute access, ~30-40% less memory per
  instance, because you drop the dict and the key-sharing machinery.
- Costs: no `__dict__`, so **no dynamic attributes** (adding
  `p.z = 3` raises `AttributeError`), no weakrefs unless you add
  `__weakref__` to the tuple, and subclasses must redeclare slots. Pickle
  protocol 2+ works; some libraries introspect `__dict__` and break.
- Dataclasses: `@dataclass(slots=True)` (3.10+) does it for you. Use
  `@dataclass(frozen=True, slots=True)` for a value object that is also
  hashable.

### 3. Hoist attribute lookups out of hot loops

CPython does not cache `obj.attr` across iterations; `LOAD_ATTR` runs every
time. Binding to a local (`LOAD_FAST` is faster) helps in very tight loops:

```python
# slower: LOAD_ATTR + method lookup per iteration
for row in rows:
    total += len(row.name) * row.weight

# faster in a hot loop: bind once
n = len
for row in rows:
    total += n(row.name) * row.weight
```

Be honest about the size of this: it is a single-digit percentage, and it
makes the code worse. Do it only where `line_profiler` already told you the
loop is the program. Never as a stylistic default.

### 4. Comprehension vs generator vs `map`

| Form | When |
|---|---|
| List comprehension `[f(x) for x in xs]` | Default. Built in C, usually the fastest for a materialised result |
| Generator expression `(f(x) for x in xs)` | When you only iterate once, or the result is huge — saves building the list, but per-item overhead is higher |
| `map(f, xs)` / `list(map(f, xs))` | Roughly on par with a comprehension; a small edge when `f` is a C function (`map(str, xs)`) |

```python
# wrong: generator consumed twice, second sum is 0
total = sum(n * n for n in data) + sum(n for n in data)
```

- A list comprehension in CPython 3.12+ is compiled to inlined bytecode and is
  genuinely fast. "Use `map` because it's faster" is stale advice for modern
  CPython.
- A generator is *not* "lazy for performance" if you immediately `list()` it —
  then it is strictly the comprehension with extra overhead.

### 5. Numeric work: `array` and numpy

Pure-Python numeric loops are the interpreter's worst case. Move the loop into
C:

- `array.array('d', ...)` — a compact typed buffer (no per-element object),
  for many numbers with no maths. Much less memory than a list of floats.
- `numpy` — for anything with maths. Vectorised, releases the GIL, and is
  orders of magnitude faster than a Python loop. This is the single biggest
  legitimate speedup in the list.
- `list` of small ints is already fine (small ints are cached). The win is for
  floats and large collections.
- Know the cost: numpy is a heavyweight dependency, and a "vectorise
  everything" change can be *slower* if you are copying big arrays per
  iteration. Measure.

### 6. String and bytes micro-costs

Formatting is a surprisingly hot cost because it runs in C but allocates:

- `f-strings` are the fastest general formatting and the clearest. Prefer them
  to `%`, `.format()`, and `str()` concatenation.
- `+` in a loop is O(n²) because each `+` builds a new string. Use
  `''.join(parts)` or an `io.StringIO`. But if the loop is small, or you can
  use f-strings, do that instead.
- `str(x)` on an int inside a hot loop is a real cost; avoid it or cache.
- `.encode()`/`.decode()` per row in a loop is a full copy each time. Do one
  bulk operation (`b"".join(parts)`, or read bytes and decode once).
- Dict lookup `d.get(k, default)` vs `d[k]`: `get` is marginally slower in the
  hit case (extra call) but avoids a `KeyError`; measure before changing.
- `"".join(list_of_bytes)` vs `b"".join(...)`: mixing `str` and `bytes` raises
  `TypeError: sequence item 0: expected a bytes-like object, str found` — a
  common bug in a micro-optimisation.

### 7. Things that are almost never the bottleneck

Do not reach for these first: `__slots__` on a class used a handful of times,
swapping a list comprehension for a generator, `gc.disable()` (it "works" by
leaking cycles into uncollected garbage and only helps allocation-heavy code
that has already been measured), caching `range` endpoints, or rewriting a
`for` as a `while`. If the profile did not point at it, you are guessing.

## Gotchas

- **`timeit` beats manual timing.** `time.time()` around a nanosecond
  operation is mostly measuring `time.time()`. Use
  `timeit.timeit(stmt, setup, number=...)` and repeat.
- **Profiling changes what you measure.** `cProfile` overhead can reorder
  bottlenecks. Use it to find the function, then measure the fix with
  `timeit` unprofiled.
- **A micro-benchmark of one function on one core will not reproduce a
  production latency problem** caused by lock contention or I/O at 200
  concurrent requests. Profile under the load that reported the problem.
- **`__slots__` breaks code that sets undeclared attributes**, including some
  test fixtures and `unittest.mock` autospec patterns. It is not free.
- **`gc.disable()` shifts the work rather than removing it** and can make
  memory unbounded. Only in a short-lived, allocation-heavy batch process, and
  only measured.
- **A tight `while` loop with a `try/except` around it is a CPython trap.**
  Before 3.11 zero-cost exceptions made this fine; in 3.11+ the setup cost
  moved but the idiom is still a `KeyboardInterrupt`/`SystemExit` hazard.
  Prefer `for` over an index-based `while` where you can.
- **The fastest code is the code you did not run.** Check for an accidental
  recomputation, a repeated `json.loads` of the same bytes, or a per-request
  re-import before micro-optimising anything.
- **Cache invalidation is a bug source.** A micro-optimisation with a
  correctness risk and a 3% gain is a net loss. Revert it.

## Handoff template

```markdown
Harness:   <command, input size, env, repeat count>
Baseline:  <p50/p95 or ops/sec, plus peak RSS>
Profile:   <tool; e.g. cProfile cumtime, top line: transform 2.1s of 2.3s>
Line:      <file:line from line_profiler — the specific cost>
Change:    <one change>
After:     <same numbers>
Verdict:   keep / revert, with the delta
```

Without the harness and baseline lines, the result is not reproducible and the
next person cannot tell a regression from noise.
