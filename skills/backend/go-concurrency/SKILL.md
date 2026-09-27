---
name: go-concurrency
description: Write Go concurrency that does not leak, deadlock, or race - goroutines as ownership, channels as hand-off, select, context cancellation and propagation, errgroup, worker pools, mutex vs RWMutex vs sync.Map, and precise loop-variable capture rules across Go versions. Use when a service hangs, grows goroutines unboundedly, deadlocks, trips the race detector, or when someone asks how to bound concurrency, fan out work, or cancel a request. Triggers on "goroutine leak", "data race", "deadlock", "context", "errgroup", "WaitGroup", "semaphore", "all goroutines are asleep".
compatibility: Go 1.22+ for loop-variable semantics; Go 1.25 adds sync.WaitGroup.Go. Verify features with `go doc` against your toolchain version.
metadata:
  version: "1.0"
---

# Go Concurrency

A goroutine is a *stack*, not a thread, and it is the cheapest resource in the
program. That is exactly why concurrency bugs in Go are about **lifetime and
ownership**, not about performance. A goroutine that is started and never
stopped is a leak; a goroutine that outlives the data it references is a data
race waiting to happen. Design for who cancels and who closes.

## Workflow

- [ ] 1. Decide what terminates every goroutine (usually: the request
      `context`)
- [ ] 2. Pass `ctx` as the first parameter of anything that blocks, and store
      it, never `context.Background()` in library code
- [ ] 3. Use `errgroup` for fan-out; `WaitGroup` only when you genuinely have
      no error and no early exit
- [ ] 4. Bound concurrency with a semaphore or a worker pool; never
      `for { go f() }` over an unbounded source
- [ ] 5. Make every channel's owner and closer explicit
- [ ] 6. Run `go test -race ./...` and `GODEBUG` leak checks before you call it
      done

## Goroutines: cheap, and that is the problem

```go
go handle(conn)          // returns immediately, conn must be captured by value
```

- Goroutines start with a ~2-8 KB stack and grow. Starting 10,000 is fine;
  starting them per request without a limit is a leak, not a feature.
- The scheduler multiplexes goroutines onto OS threads, so blocking on a
  channel is not a thread block.
- **Nothing stops a goroutine automatically.** When the function that spawned it
  returns, the goroutine keeps running against whatever it captured. If those
  are stack variables, it is reading freed-looking memory; if they are a
  request struct, it is a use-after-free in spirit.

### Loop variable capture — the version rule

This is the most-copied Go gotcha, and the fix landed in **Go 1.22**. Be
precise:

- **Go 1.22+**: each iteration of a `for` loop creates a **new** variable for
  the loop variable. Capturing `i` or `v` in a goroutine or a closure gives each
  iteration its own copy. The classic
  `for _, v := range xs { go func() { use(v) }() }` bug is **fixed**. The loop
  variables are per-iteration.
- **Go 1.21 and earlier**: the loop variable is a **single** variable shared by
  all iterations. Every closure/goroutine that captures it sees whatever value
  it has when it eventually runs (usually the last one), or worse, a torn
  value under `-race`. You had to shadow it: `v := v` inside the loop.
- The old `v := v` shadow trick is now **unnecessary** on 1.22+ and is harmless.
  It is also misleading. If your `go.mod` says `go 1.22` or later, you can drop
  it; if you support older toolchains, keep it.

Check your module's language version: `go.mod`'s `go 1.22` line (not the
toolchain you happen to have installed) is what selects this behaviour. A `go
1.21` line in `go.mod` under a Go 1.24 toolchain still uses the **old**
per-loop-variable semantics. This is the trap: the fix keys off the language
version in `go.mod`, not the compiler version.

Capturing a **named return** or a variable you then mutate is still a race on
every version; the 1.22 fix is only about the *loop* variable. A pointer to a
struct you keep mutating is a race regardless.

## context: cancellation and values

`context.Context` is how cancellation, deadlines, and request-scoped values
flow down a call tree.

```go
func Fetch(ctx context.Context, id string) (User, error) {
    // FIRST parameter, named ctx, never stored in a struct.
}
```

- **Pass the parent's `ctx` down**, derive a child when you add a deadline or
  cancel: `ctx, cancel := context.WithTimeout(ctx, 2*time.Second); defer
  cancel()`. The `defer cancel()` is not optional — a missing one leaks the
  timer and the parent's child list until the parent dies.
- **In library code, never create a fresh `context.Background()`** for a
  caller's work; that detaches the work from the caller's cancellation and is
  the root of most goroutine leaks. The *main* function and test setup are the
  only correct places for `context.Background()` / `context.TODO()`.
- **Do not put required data in a `context.Value`.** `ctx.Value` is for
  request-scoped, optional, cross-cutting data (a trace id, a logger, an
  authenticated principal) that a deep callee may need but that is not part of
  the function's job. If the function *needs* the data to do its work, it is a
  **parameter**. A value in a context is invisible in the function signature,
  untypeable, impossible to require, and the compiler will not help you. Use
  `context.WithValue` with an unexported custom key type
  (`type ctxKey struct{}`) to avoid collisions.
- When a context is cancelled, functions doing blocking work should return
  `ctx.Err()` (`context.Canceled` or `context.DeadlineExceeded`).
  `context.DeadlineExceeded` is a timeout and may be retryable;
  `context.Canceled` usually means the caller went away and is not.
- `http.NewRequestWithContext(ctx, ...)` is mandatory for outgoing HTTP. The
  bare `http.NewRequest` gives you a request you cannot cancel, and the
  connection outlives the request.

## Fan-out: `errgroup`

`errgroup.Group` runs a set of goroutines, collects the **first** error,
cancels a shared context when any one fails, and `Wait()`s for all. This is
almost always what you want.

```go
import (
    "context"
    "fmt"
    "golang.org/x/sync/errgroup"
)

func FetchAll(ctx context.Context, ids []string) ([]User, error) {
    g, ctx := errgroup.WithContext(ctx)   // ctx is cancelled on first error
    users := make([]User, len(ids))        // each goroutine writes its own index
    for i, id := range ids {
        i, id := i, id                     // needed only for go.mod < 1.22
        g.Go(func() error {
            u, err := Fetch(ctx, id)
            if err != nil {
                return fmt.Errorf("fetch %q: %w", id, err)
            }
            users[i] = u                  // disjoint indices: no race
            return nil
        })
    }
    if err := g.Wait(); err != nil {
        return nil, err                    // siblings already cancelled
    }
    return users, nil
}
```

- `g.Wait()` returns the first non-nil error; because `WithContext` cancelled
  the shared `ctx`, the other operations abort promptly. Without the shared
  ctx, `Wait` still waits for all goroutines to finish, so a slow sibling
  defeats your timeout.
- `g.SetLimit(n)` (from `x/sync`) caps concurrency without a separate
  semaphore — blocks when the limit is reached. Use it before `g.Go`; the
  error `TryGo` variant returns `false` instead of blocking.
- `errgroup` is in `golang.org/x/sync`, not the stdlib. It is the de-facto
  choice; `sync.WaitGroup` is the stdlib alternative when you have no error
  and no cancellation.

### `WaitGroup` — when you actually want it

```go
var wg sync.WaitGroup
for _, item := range items {
    wg.Add(1)
    go func() {
        defer wg.Done()
        process(item)                    // no error path, no cancellation
    }()
}
wg.Wait()
```

- `wg.Add(1)` must happen **before** the `go`, not inside it, or `Wait` can
  return before the goroutine is counted.
- **`Wait` with a zero counter returns immediately** — a common bug where you
  conditionally `Add` in a branch and some iterations do not.
- Note: `go vet`'s `lostcancel` and the `sync.WaitGroup.Go` method (**Go
  1.25+**, `wg.Go(func(){...})` which does `Add`/`Done` for you) reduce the
  foot-guns. Verify `wg.Go` exists for your toolchain (`go doc
  sync.WaitGroup.Go`); do not use it on older versions.

## Channels, and who closes them

A channel is a **hand-off and a synchronisation point**, and the discipline is:

- **The sender closes**, and only the sender, exactly once. A receiver never
  closes. Double-close is a `panic: close of closed channel`; closing a nil
  channel panics.
- Closing signals "no more values will be sent". Receivers detect it as the
  **comma-ok zero value** (`v, ok := <-ch`; `ok == false` means closed), or by
  a range loop ending.
- Use a **buffered** channel of size 1 as a one-shot signal (a `done` channel)
  to mean "close, not send", so the receiver's `<-done` unblocks without a
  send. This is the idiomatic shutdown/close signal — do not `close(ch)` to
  wake a receiver when you mean "signal once"; closing is for "no more ever".

```go
done := make(chan struct{})     // closed to broadcast
go func() { defer close(done); work() }()
<-done                          // unblocks every receiver
```

- A **send on a full channel blocks**; a **send on a closed channel panics**;
  a **receive on a nil channel blocks forever**; a **close of a nil channel
  panics**. A nil channel is sometimes useful — a `nil` case in a `select` is
  simply never ready, which disables that branch.
- **Unbuffered by default** (`make(chan T)`) is a synchronisation point: the
  send blocks until a receiver takes it. **Buffered** (`make(chan T, n)`
  decouples producer and consumer by up to `n`. A buffer of 1 or a small value
  smooths a pipeline; a huge buffer hides backpressure and just moves the
  memory spike. Buffer to smooth bursts, not to avoid blocking.
- **Never close a channel you did not create**, and never return an
  unexported channel a caller might close. Signal with the channel being
  closed *by you* in a `defer` inside the constructor/owner, or return a
  receive-only channel (`<-chan T`) to prevent the caller from closing it.

### `select`: multiplexing and cancellation

```go
for {
    select {
    case <-ctx.Done():
        return ctx.Err()               // the ONLY way a goroutine stops on cancel
    case v, ok := <-results:
        if !ok {
            return nil                 // producer closed the channel: done
        }
        consume(v)
    case out <- v:
        // send case
    }
}
```

- **`select` chooses randomly among ready cases.** Two cases ready →
  coin flip, every time. Never write a `select` whose correctness depends on
  which branch wins.
- A `select` with **no default blocks** until a case is ready. With a
  `default:` it is a non-blocking poll that runs the default when nothing is
  ready. Use the `default` form for a "drain what's ready" loop.
- **Always include a `case <-ctx.Done()`** in a `select` inside a long-running
  goroutine, or it will never notice cancellation. This is the single most
  important habit.
- A send in a `select` fires only if a receiver is *already* waiting (for an
  unbuffered channel); otherwise that case is not ready. So a
  "writer goroutine + reader that starts later" can deadlock unless the channel
  is buffered.

Full pattern cookbook (fan-in, fan-out, the complete cancellable worker pool,
pipeline stages, done/broadcast, timers, nil-channel trick, `select` + default
polling, non-blocking send attempt, semaphore bounding):
`references/channel-select-cheatsheet.md`.

## Worker pools and bounded concurrency

Unbounded `go f()` over an unbounded source is how you OOM a service. The
worker template — every worker does a two-case `select` (`ctx.Done()` or
`jobs`) so cancellation is honoured, and the producer `close(jobs)` on every
path — is in `references/channel-select-cheatsheet.md`.

The essentials:

```go
for w := 0; w < workers; w++ {         // workers = GOMAXPROCS(0)*2, tune it
    wg.Add(1)
    go func() {
        defer wg.Done()
        for {
            select {
            case <-ctx.Done():
                return                // workers must be cancellable
            case j, ok := <-jobs:
                if !ok {
                    return            // producer closed jobs: done
                }
                handle(ctx, j)
            }
        }
    }()
}
defer close(jobs)                      // exactly once, on every path
```

- Bound the fan-out. Either a fixed worker pool, or `errgroup.SetLimit(n)`, or
  a **counting semaphore** (`sem := make(chan struct{}, n)`, `sem <- struct{}{}`
  before, `<-sem` after) for a fixed number of goroutines over a dynamic
  stream.
- `close(jobs)` must happen exactly once, on every path (including the
  cancellation path above) — use `defer close(jobs)` if the producer is a
  single function.
- If workers only ever `range jobs` without a `ctx.Done()` case, the pool
  cannot be cancelled: a `ctx` cancel leaves them blocked on `<-jobs` until the
  producer closes the channel. That is why the two-case `select` is the
  template.

## Mutex vs RWMutex vs sync.Map

| Tool | Protects | Use when |
|---|---|---|
| `sync.Mutex` | Any state, one holder at a time | **Default.** Write-heavy or mixed |
| `sync.RWMutex` | Any state, many readers or one writer | Read-mostly and the critical section is long enough that the extra atomic ops pay off |
| `sync.Map` | A specific `map[K]V` | See below — rarely the right choice |

```go
type Cache struct {
    mu sync.RWMutex
    m  map[string]Entry
}
func (c *Cache) Get(k string) (Entry, bool) {
    c.mu.RLock(); defer c.mu.RUnlock()
    e, ok := c.m[k]            // map reads are safe under RLock
    return e, ok
}
func (c *Cache) Set(k string, e Entry) {
    c.mu.Lock(); defer c.mu.Unlock()
    c.m[k] = e
}
```

- **A plain `map` is not safe for concurrent use**; concurrent read+write
  causes a runtime `fatal error: concurrent map writes` (which is not
  recoverable and kills the process) or a silent race. Always guard it, or
  make it per-goroutine.
- **`RWMutex` is not always faster than `Mutex`.** It has higher per-operation
  cost; it only wins when reads dominate *and* the read section is non-trivial.
  Under mixed load it can be slower. Measure. Also: a writer waiting blocks
  new readers, so a long-held `RLock` can starve writers — never do I/O or
  channel sends while holding any lock.
- **`defer mu.Unlock()` immediately after `Lock`.** It is correct under early
  returns and panics, and the `Unlock` cost is negligible.
- **Never copy a `sync.Mutex`/`RWMutex`.** Copying the struct (passing by
  value) copies the lock and breaks mutual exclusion. Keep it as a zero field
  and pass the struct by pointer.
- **`sync.Map` is a specialised, mostly-append-only map** optimised for two
  cases: (1) keys written once and read many times (e.g. a cache that mostly
  misses and is only ever added to), or (2) disjoint key sets across
  goroutines. The type is `sync.Map` with `any` keys/values, so you lose
  static typing. It is **not** a general replacement for `map` + `Mutex`; the
  doc explicitly says a `Mutex`-protected `map` is better for a
  write-once/read-many or type-safe map. Default to `map` + `RWMutex`.

## Goroutine leaks — how to find them

- **A leak is a goroutine blocked forever** on a channel send/receive, a
  `select`, or a `Wait` that nobody will satisfy. Every spawned goroutine must
  have a path to return.
- Diagnostic: `runtime.NumGoroutine()` at a point in time; if it grows
  monotonically over a soak test, you are leaking.
- Dumps: send `SIGQUIT` (`kill -QUIT <pid>`) to a Go process to get a full
  goroutine stack dump, or `go tool pprof http://localhost:6060/debug/pprof/goroutine?debug=2`.
  Look for goroutines all parked `chan receive` on the same channel.
- `go.uber.org/goleak` (a test-only dependency) fails a `TestMain` if
  goroutines outlive the test — the best defence.
- The `GODEBUG` `schedtrace` isn't the leak tool; use the pprof goroutine
  endpoint.

## Race detector and atomics

- **Always run tests with `-race`**: `go test -race ./...`. It finds real races
  that pass without it and are rare in production. CI must run it.
- A data race means undefined behaviour — not "probably fine". Even if it
  looks atomic on x86, it is not on ARM.
- **`sync/atomic`** (`atomic.Int64`, `atomic.Bool`, `atomic.Pointer[T]`,
  `Add`, `Load`, `CompareAndSwap`) for simple counters and flags. `atomic.Int64`
  has the typed API (Go 1.19+); the old `atomic.AddInt64(&x, 1)` is legacy.
  A mutex is still fine and often clearer; reach for atomics only when a
  profiler says the lock is contended.
- Passing a value through a channel, or protecting it with a mutex, is the
  normal way to share; an atomic is for a single scalar.
- **`copy` of a slice header while another goroutine appends to the backing
  array** is a classic race that `-race` may or may not catch depending on
  timing. Pass slices by value and treat the backing array as immutable, or
  lock.

## Gotchas

- **Loop variable capture is fixed only in Go 1.22+ *and* only if `go.mod`
  says `go 1.22` or higher.** Key off the language version, not the toolchain.
- **`ctx` must be the first parameter** of a blocking function, and passed, not
  stored in a struct — storing freezes a request context on a long-lived
  object and cancellation stops working.
- **A missing `defer cancel()`** on a `WithTimeout`/`WithCancel` leaks the
  timer and keeps the parent alive. `go vet` flags this; CI must run `go vet`.
- **`select` with two ready cases is nondeterministic.** Design so it does not
  matter, or handle both.
- **A `for` loop with no `ctx.Done()` case, or a `range` over a channel that is
  never closed, is a hang.** Every channel a consumer ranges over must be
  closed by its producer on every path.
- **`sync.Mutex` must not be copied** by value after first use.
- **`time.Ticker` must be stopped** (`defer t.Stop()`) or it leaks a runtime
  timer. `time.After` in a loop allocates a timer per iteration that lives
  until it fires — prefer a reused `Ticker` or `NewTimer` in long loops.
- **Goroutines leak silently.** There is no runtime error, just growing memory
  and goroutine count. Test for them (`goleak`), do not wait for production.
- **`errgroup.Wait` returns only the first error**; if you need to know about
  several failures, collect them explicitly (e.g. `errors.Join`).
- **Closing a channel does not stop a goroutine blocked sending on it.** The
  sender panics; the receiver drains. A cancellation that closes the wrong
  direction is a panic, not a shutdown.
- **`panic` in a goroutine you spawned kills the whole process** unless you
  `recover` inside that goroutine (not in the parent). See `go-error-handling`.
- **`recover` only works in a deferred function** directly in the panicking
  goroutine; it does not propagate to the parent goroutine.
