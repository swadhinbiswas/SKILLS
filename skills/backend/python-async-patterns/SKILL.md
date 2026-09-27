---
name: python-async-patterns
description: Write asyncio that does not block, leak, or silently serialise - await semantics, tasks vs coroutines vs futures, TaskGroup structured concurrency, timeouts, cancellation (including shield and uncancel), async context managers, and running blocking code via asyncio.to_thread or a process pool. Use when code is "hanging", "slow under load", "cancelled but the work keeps running", or when a user says "why is my async code not faster", "event loop blocked", "asyncio.gather", "TaskGroup", "run_in_executor". Triggers on "async def", "await", "asyncio", "deadlock", "no concurrency", "everything is serial".
compatibility: Python 3.11+ (TaskGroup, asyncio.timeout, to_thread are 3.11+). Examples use stdlib asyncio only; httpx for HTTP.
metadata:
  version: "1.0"
---

# Python Async Patterns

`async` buys you concurrency, not parallelism, and only if nothing blocks the
loop. A single sync call inside an `async def` undoes every benefit and does it
silently — no warning, no traceback, just an application that scales to one
request at a time.

## Workflow

- [ ] 1. Decide whether the work is I/O-bound or CPU-bound — it picks the whole
      design
- [ ] 2. Write `async def` all the way down; no sync library calls anywhere
- [ ] 3. Structure the fan-out with `asyncio.TaskGroup`, never bare
      `gather` on the happy path
- [ ] 4. Put a `asyncio.timeout` around every external call
- [ ] 5. Check cleanup runs on cancellation (`finally`, `contextlib.aclosing`,
      async context managers) — not just on success
- [ ] 6. Load-test with concurrency, not sequentially; a serial benchmark
      proves nothing

## The one rule: never block the loop

The event loop is a single-threaded scheduler. Anything synchronous inside it
stalls **every** coroutine, not just the one you are in.

```python
import time, requests, httpx

# BROKEN. time.sleep blocks the OS thread. The loop cannot run any other
# coroutine for 1 second — every concurrent request stalls with it.
async def fetch_user_sync(user_id: int) -> dict:
    return requests.get(f"https://api/users/{user_id}").json()

async def wait_sync() -> None:
    time.sleep(1)          # silently blocks the whole loop

# ALSO BROKEN, and worse: the sync DB driver.
async def load_order(order_id: str) -> "Order":
    return await order_repository.get(order_id)   # if that repo is psycopg2/SQLAlchemy sync
```

The loop is not blocked by `time.sleep` specifically — it is blocked by *any*
sync work over a scheduling quantum. A 2ms CPU loop is 2ms of zero throughput
for the whole process. `requests.get()` is the classic offender: it is a
convenient, well-known library that is entirely synchronous.

Detect it: `py-spy dump --pid <pid>` and look for sync frames under
`asyncio`, or the `debug` mode warning
`Executing <Handle...> took 0.10 seconds`. See `python-performance` for the
profiling commands.

## The blocking-call decision

| The call is… | Do this |
|---|---|
| A third-party sync I/O library | `await asyncio.to_thread(fn, ...)` |
| A `time.sleep` in a demo/test | `await asyncio.sleep(n)` |
| A sync DB/ORM driver | Use the native async driver (`asyncpg`, `psycopg` async, `sqlalchemy.ext.asyncio`). `to_thread` fixes the loop but ties up a thread per connection |
| CPU-bound, short (<10ms) | `to_thread` is fine — the default |
| CPU-bound, long, or needs real parallelism | `ProcessPoolExecutor`; the GIL means a thread will not help |
| Pure-Python maths over big arrays | Rewrite with numpy; the GIL is not your enemy, the interpreter loop is |

```python
import asyncio, time
from concurrent.futures import ProcessPoolExecutor

# Default: offload a blocking call to the loop's thread pool.
total = await asyncio.to_thread(sum, range(10_000_000))

# CPU-bound and long-running: processes, not threads.
def hash_file(path: str) -> bytes:
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).digest()

loop = asyncio.get_running_loop()
with ProcessPoolExecutor() as pool:
    digests = await asyncio.gather(*[
        loop.run_in_executor(pool, hash_file, p) for p in paths
    ])
```

Limits that bite in production:

- `to_thread` uses the loop's **default executor**, whose size is
  `min(32, os.cpu_count() + 4)` (Python 3.8+). Queue beyond that and you are
  queued, not concurrent. A handler that fans out to 200 blocking calls gets
  32 at a time.
- The default executor is **shared process-wide**. One component saturating it
  starves every other `to_thread` caller, including libraries using it
  internally. If you need your own pool, create an explicit
  `ThreadPoolExecutor` and pass it to `loop.run_in_executor(pool, ...)`.
- Process pools cannot send arbitrary objects. Arguments and results are
  pickled — a DB connection, socket, or lambda will raise at submit time. Pass
  paths and ids, not live objects. Spawn cost is ~50-500ms; batch work.
- A `ProcessPoolExecutor` started with the default `fork` start method in a
  process that has already initialised threads or a DB pool can deadlock in
  the child. Use `mp_context=multiprocessing.get_context("spawn")` in servers.

## async / await semantics

- `await` suspends **the current coroutine**, not the thread. The thread keeps
  running other coroutines.
- An `async def` function **calling** one just creates a coroutine object and
  returns. Nothing runs until it is awaited or scheduled. Forgetting the
  `await` produces a `RuntimeWarning: coroutine 'foo' was never awaited` and a
  function that quietly did nothing.
- `asyncio.create_task(coro)` schedules it and returns a `Task` immediately.
  **The task is weakly referenced by the loop** — if you do not keep a
  reference, it can be garbage-collected mid-flight. Always
  `t = asyncio.create_task(...)` and hold `t` (a set, or a `TaskGroup`).
- Awaiting the same coroutine object twice raises
  `RuntimeError: cannot reuse already awaited coroutine`. A coroutine is a
  single-use object, not a re-runnable function. A `Task` *is* re-awaitable.
- `asyncio.run()` at the top level creates and closes a loop. You cannot call
  it from inside a running loop — that is
  `RuntimeError: asyncio.run() cannot be called from a running event loop`.
  In a notebook, use `await` at top level instead.

## Tasks, coroutines, and futures

| Object | What it is | Re-awaitable | Cancelable before done | Typical use |
|---|---|---|---|---|
| Coroutine | Lazy object from `async def` | No — errors on 2nd await | No | The function you write |
| `Task` | Coroutine scheduled on the loop | Yes | Yes | Fire-and-observe fan-out |
| `Future` | A value that will be set, no code attached | Yes | Yes | Bridge from a callback API |
| `asyncio.Event` / `Lock` | Primitives for coordination | n/a | n/a | Shutdown signals, mutual exclusion |

- `asyncio.gather` returns a list of results and, with `return_exceptions=False`
  (the default), raises the **first** exception — leaving every other task
  **running and un-awaited**. That is a leak. Use `return_exceptions=True` if
  you truly want all outcomes, or better, use `TaskGroup`.
- `wait_for(coro, timeout)` wraps in a timeout and cancels on expiry. On 3.12+
  it waits for the cancellation to actually take effect. On 3.11 it returns
  immediately after requesting cancel, so the inner cleanup may still be
  running (see the cancellation table in
  `references/cancellation-semantics.md`).
- Never `await` inside a `finally` that runs during cancellation without
  `asyncio.shield`, or the cleanup can be cancelled too and leak a connection.

## Structured concurrency with TaskGroup (3.11+)

`asyncio.TaskGroup` is the default for fan-out. It gives you: children are
cancelled automatically if one fails, no child can outlive the group, and the
raised exception is an `ExceptionGroup` (a `BaseExceptionGroup` subclass),
never a bare first error.

```python
import asyncio

async def gather_all(user_ids: list[str]) -> list[dict]:
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(fetch_user(uid)) for uid in user_ids]
    return [t.result() for t in tasks]
```

- If any child raises, the group cancels the rest and raises an
  `ExceptionGroup`. Catch it and unpack:
  ```python
  try:
      async with asyncio.TaskGroup() as tg: ...
  except* TimeoutError as eg:
      ...  # one or more children timed out
  ```
  `except*` is 3.11 syntax; the plain `except*` group handling does not need any
  library.
- Cancellation of the group by an *outer* cancel is honoured: the children get
  cancelled and the group does not swallow it.
- Do **not** use `gather` just because TaskGroup "returns a list differently".
  TaskGroup does not return the results; you read `task.result()` on the tasks
  you created. That is the price for automatic cancellation and no orphans.
- `TaskGroup` in a library that must support 3.10 and below: use `gather` and
  cancel siblings in `finally`, or pin the floor at 3.11.

## Timeouts and cancellation

```python
import asyncio

# Preferred on 3.11+: a context manager, so the timeout scope is obvious and
# composes with other timeout scopes (innermost wins).
async with asyncio.timeout(2.0):
    await slow_call()

# Catching TimeoutError converts it to a normal exception. Catching
# CancelledError does NOT and is almost always a bug.
```

The trap: **`shield` and `uncancel`.** `asyncio.shield` protects an inner
coroutine from cancellation of the *outer* one — but the inner task keeps
running after the outer gives up. In a request handler that is a background
goroutine you can no longer observe, and if it touches a connection the
request already returned, you have a use-after-free style bug in Python
clothing.

```python
# Looks like it prevents a double-charge. It does not: if the client
# disconnects mid-charge, cancel() is delivered here, shield() absorbs it,
# wait_for() raises TimeoutError, the handler returns — and the charge
# coroutine is STILL RUNNING with no owner. It may complete, or throw into
# a dead task nobody reads.
await asyncio.wait_for(asyncio.shield(charge(order)), timeout=1.0)
```

Rules:

- Shield only when the work **must** finish and has its own error handling and
  its own lifecycle (a durable write to an outbox you will reconcile).
- If you shield, keep a reference to the inner task and await it somewhere you
  own (a background set drained at shutdown), or you have created a leak.
- On 3.11+, if you catch `CancelledError` and decide *not* to re-raise, you
  **must** call `task.uncancel()`. Skipping it leaves the task in a cancelling
  state, so the next `await` re-raises `CancelledError` and any enclosing
  `timeout`/`TaskGroup` miscounts it.
- Cleanup in `finally` after a cancel should be short, non-blocking, or
  shielded. A `finally` that awaits for 5 seconds delays the cancellation of
  the whole request.

Full table of what gets cancelled, what does not, and the 3.11 vs 3.12/3.13
differences: `references/cancellation-semantics.md`.

## Async context managers

`async with` is not optional for async resources — `with` on an async object
does not await `__aenter__` and leaks the connection.

```python
class Pool:
    async def __aenter__(self) -> "Conn": ...      # acquire
    async def __aexit__(self, *exc) -> None: ...   # release
    async def __aiter__(self): ...                 # async iteration
    async def __anext__(self): ...
```

- Library clients expose `async with client.stream(...) as resp:` for
  streaming, and `async with aiohttp.ClientSession() as s:` for pools. Always
  use the context manager form; a manually-closed `httpx.AsyncClient` leaks
  sockets until GC.
- **Iterate async iterables with `async for`, never a plain `for`.** A plain
  `for` over an async generator raises
  `TypeError: 'async_generator' object is not iterable`.
- A generator-based context manager (`@contextmanager`) is sync-only. For an
  async one, use `@asynccontextmanager` from `contextlib`, or write
  `__aenter__`/`__aexit__` for full control.
- `contextvars` and `contextlib` are the async equivalent of thread-locals for
  request ids and log context; plain thread-locals are shared by all coroutines
  on the loop.

## Gotchas

- **`asyncio.sleep(0)` still yields to the loop** — use it in a spin-wait to
  stay cooperative. `while True: pass` without any await hangs the process
  forever.
- **Fire-and-forget `create_task` without a reference is collected.** The loop
  holds only a weak reference; the task can vanish mid-`await`. Hold it.
- **`asyncio.run()` closes the loop**, which finalises the default executor and
  cancels nothing you started outside it. Any leftover task logs
  "Task was destroyed but it is pending!".
- **Two event loops cannot share an object.** An `aiohttp` session or a DB pool
  created on loop A and used on loop B deadlocks or raises. One loop per
  process, one session per loop, created in the startup hook.
- **`httpx.AsyncClient` without a timeout hangs forever** on a slow server;
  pass an explicit `timeout=httpx.Timeout(...)`.
- **`asyncio.Queue` is unbounded by default** (`maxsize=0`). A producer faster
  than a consumer grows it without limit and OOMs. Set a `maxsize` and await
  `put` so the producer blocks.
- **Exceptions in a background task are silent** until you await the task or
  call `task.add_done_callback`. A task that raised overnight prints nothing.
- **`uvloop` is a drop-in** (`uvloop.run(main())`) and meaningfully faster for
  many-connection servers. It is a separate dependency; verify it supports your
  Python version before requiring it.

## Files

- `references/cancellation-semantics.md` — a table of what cancels, what
  `shield` protects, `wait_for` vs `timeout` across 3.11/3.12/3.13, and the
  `uncancel` rule. Read it when a task is not stopping or a cancellation
  appears to be swallowed.
