# Cancellation and timeout semantics

What cancels, what does not, and how it changed. Read this when a task is not
stopping, a `CancelledError` is being swallowed, or a timeout seems to leave
work running. Versions are CPython 3.11 (minimum here) and 3.12/3.13.

## The mechanism

- A task can hold at most one pending cancellation request. `task.cancel()`
  increments a counter; if the task is suspended on a future, that future is
  cancelled and the task resumes by raising `CancelledError` at the `await`
  point.
- `CancelledError` inherits from `BaseException`, not `Exception`. A bare
  `except Exception:` will **not** catch it — that is deliberate, so a
  `CancelledError` is not silently absorbed by generic error handling. But
  `except BaseException:` or a bare `except:` will, and swallowing it there is
  a real bug.
- A coroutine that catches `CancelledError` and returns normally finishes as
  **cancelled** (the task ends in the cancelled state) — the return value is
  discarded and the task is not marked successful.

## Who cancels what

| Construct | Cancels the inner work on scope exit? | Notes |
|---|---|---|
| `asyncio.timeout(t)` | Yes, on expiry it cancels the current task and converts to `TimeoutError` | Preferred; scopes compose (innermost expiry wins) |
| `asyncio.wait_for(coro, t)` | Yes, requests cancel and waits | 3.11 returns as soon as cancel is *requested*; 3.12+ waits for the cancel to take effect |
| `asyncio.TaskGroup` | Yes, a failing child cancels the siblings; an external cancel cancels all children | Siblings get `CancelledError`; unwrap with `except*` |
| `asyncio.gather` (default) | No | First exception propagates, **other tasks keep running** — orphans |
| `asyncio.gather(..., return_exceptions=True)` | No | Waits for all, so no orphans, but a cancel still cancels the children |
| `asyncio.shield(inner)` | No | Protects `inner` from *outer* cancel; `inner` keeps running, unowned, after the outer gives up |
| `Task.cancel()` | Requests cancel at the next `await` | Cooperative: cannot interrupt pure CPU/sync code until it next yields |
| `loop.stop()` / process exit | No | Pending tasks are destroyed → "Task was destroyed but it is pending!" |
| `asyncio.Semaphore`/`Lock` release | n/a | Never `await` inside a critical section that can be cancelled without re-checking |

## The shield trap, precisely

```python
await asyncio.wait_for(asyncio.shield(work()), timeout=1.0)
```

- If `timeout` expires first: `shield`'s outer future is cancelled,
  `wait_for` raises `TimeoutError`, but **`work()` is still running** as an
  independent task with no owner. The caller's `async with` / request scope has
  already torn down anything `work()` depended on.
- 3.12+ `wait_for` waits for the *wrapper* to finish, not for the shielded
  inner task. The inner still outlives it.
- This is only correct when `work()` is a durable, self-contained write whose
  failure is handled inside it. It is wrong for anything touching a request
  scoped connection, socket, or transaction.

Safe alternatives:

1. Let it cancel and put the must-survive work on a real queue you own and
   drain at shutdown.
2. Shield **and** keep the task:
   ```python
   background: set[asyncio.Task] = set()
   t = asyncio.create_task(work())
   background.add(t); t.add_done_callback(background.discard)
   try:
       await asyncio.wait_for(asyncio.shield(t), timeout=1.0)
   except TimeoutError:
       t.add_done_callback(lambda _: log_result(t))   # observe it
   ```
3. Do the durable write in the handler and only the *response* in the
   timeout scope.

## The uncancel rule (3.11+)

If you catch `CancelledError` and choose **not** to propagate it, the task is
still flagged as cancelling. Any enclosing `asyncio.timeout` or `TaskGroup`
counts the uncancelled request and may convert a later cancel into a spurious
`TimeoutError`, or a `TaskGroup` may refuse to exit cleanly.

```python
task = asyncio.current_task()
try:
    await something()
except asyncio.CancelledError:
    if not keep_going:
        raise
    task.uncancel()          # only if you genuinely absorb the cancel
    return partial_result
```

Call `uncancel()` exactly once per caught-and-absorbed `CancelledError`, and
only when you really are swallowing it. The common case — catch, log, re-raise
— needs no `uncancel`.

## wait_for vs timeout (3.11 → 3.13)

- `asyncio.timeout` (3.11+): a context manager. On expiry it cancels the
  current task and raises `TimeoutError` from the `async with` line. Because it
  cancels the *task*, any code that catches `CancelledError` inside can
  interfere; that is the `uncancel` situation above.
- `wait_for`: wraps a specific awaitable, so it does not cancel the calling
  task — it cancels the wrapped future. 3.11 returns once the cancel is
  requested, so the inner cleanup may still be in flight when `wait_for`
  raises. 3.12 changed it to wait for the cancellation to actually complete,
  so the inner has unwound. Either way it does not stop a *shielded* inner.
- Both raise `asyncio.TimeoutError` (an alias of the builtin `TimeoutError`
  since 3.11). Catch `TimeoutError`, not a bare `except Exception` around a
  `try/except` that also has an unrelated `TimeoutError` from a network call.

## Timeout every external boundary

Nothing in production has an infinite deadline. Wrap each await on I/O:

```python
async with asyncio.timeout(connect_timeout):   # e.g. 2s to establish
    conn = await pool.acquire()
async with asyncio.timeout(request_timeout):   # e.g. 10s for the op
    return await conn.execute(sql)
```

A hung TCP connection with no timeout holds the request open forever, and
because the whole app is coroutines on one thread, "forever" applies to the
process's concurrency budget too.

## Diagnosing a task that will not cancel

- `CancelledError` cannot preempt running sync code. If the task is inside a
  blocking call, it will not be delivered until that call returns and the task
  next hits an `await`.
- If the cancellation is delivered but the task stays alive, the task is
  probably in a `finally` that awaits (a slow close). That await is itself
  cancellable in a loop; shield or bound it with a short `timeout`.
- Print `asyncio.all_tasks()` (3.12+) or
  `asyncio.all_tasks(loop)` and their stacks to see where each task is parked.
- If the code path catches `BaseException` or bare `except:`, that is the
  swallow. Search for `except:` and `except BaseException` first.
