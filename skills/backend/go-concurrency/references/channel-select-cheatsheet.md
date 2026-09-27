# Channel and select cookbook

Copy-adaptable patterns. Read this when wiring a pipeline, fanning out to many
consumers, or debugging a hang. Every snippet compiles as-is; the comments are
the part to read.

## Fan-in: N producers, one consumer

```go
func merge(cs ...<-chan int) <-chan int {
    out := make(chan int)
    var wg sync.WaitGroup
    wg.Add(len(cs))
    for _, c := range cs {
        go func(c <-chan int) {
            defer wg.Done()
            for v := range c {     // ends when c is closed by ITS owner
                out <- v
            }
        }(c)
    }
    go func() { wg.Wait(); close(out) }()   // out's owner closes it
    return out
}
```

The rule this demonstrates: **whoever created `out` closes it**, and that
happens only after every producer has returned. Returning a bidirectional
channel lets the caller close it and panic; return `<-chan int`.

## Fan-out: one producer, N workers

```go
func fanIn(in <-chan int, out chan<- int, workers int) {
    var wg sync.WaitGroup
    for i := 0; i < workers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for v := range in {
                out <- v * 2
            }
        }()
    }
    wg.Wait()
    close(out)     // still the owner's job, after all workers finish
}
```

## Pipeline stages

```go
func stage(in <-chan int, fn func(int) int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)             // always, including on early return
        for v := range in {
            select {
            case out <- fn(v):
            case <-time.After(time.Second):
                return                // stop the stage if downstream stalls
            }
        }
    }()
    return out
}
```

Every stage: goroutine in, `defer close(out)`, `range in`, and a send.
Compose: `stage(stage(ch, f), g)`.

## Done / broadcast / shutdown

```go
done := make(chan struct{})          // closed, never sent on

// broadcast to many
for i := 0; i < 10; i++ {
    go func() { <-done; cleanup() }()
}
close(done)                          // all ten unblock

// request/response over one channel (request struct carries a reply)
type req struct { q string; reply chan string }
func worker(ch <-chan req) {
    for r := range ch { r.reply <- compute(r.q) }   // owner closes ch
}
```

- `done` is **closed**, not sent on — that is what makes it a broadcast.
- A per-request `reply chan string` inside a request struct is the standard
  "one response, many consumers" shape.

## Timers in a loop (avoid `time.After`)

```go
// BAD: allocates a timer every iteration; the timer lives until it fires.
for {
    select {
    case <-time.After(5 * time.Second):
        return
    case v := <-ch:
        use(v)
    }
}

// GOOD: one timer, stopped on every exit path.
t := time.NewTimer(5 * time.Second)
defer t.Stop()
for {
    select {
    case <-t.C:
        return
    case v := <-ch:
        use(v)
        if !t.Stop() { select { case <-t.C: default: } }   // drain, then Reset
        t.Reset(5 * time.Second)
    }
}
```

## The nil-channel trick

A `nil` channel is never ready. A `nil` case in a `select` simply never fires,
which lets you disable a branch at runtime:

```go
var retry <-chan time.Time        // nil by default
if shouldRetry {
    retry = time.After(backoff)
}
select {
case v := <-result:
    return v
case <-retry:
    return ErrRetry
case <-ctx.Done():
    return ctx.Err()              // the nil case is just ignored
}
```

## Non-blocking send / drain

```go
// "Send if someone is ready; drop otherwise." Used for lossy latest-value.
select {
case out <- v:
default:
    // drop: downstream is busy, we prefer the newest value anyway
}

// Drain everything currently ready, then stop (non-blocking poll loop).
for {
    select {
    case v, ok := <-ch:
        if !ok { return }
        use(v)
    default:
        return
    }
}
```

## The full worker-pool template

```go
type Job struct{ ID int; Payload string }
type Result struct{ ID int; Err error }

func Run(ctx context.Context, jobs []Job, workers int) ([]Result, error) {
    g, ctx := errgroup.WithContext(ctx)
    g.SetLimit(workers)                       // caps concurrency
    results := make([]Result, len(jobs))
    for i, j := range jobs {
        i, j := i, j                           // only needed if go.mod < 1.22
        g.Go(func() error {
            r, err := handle(ctx, j)          // handle honours ctx
            results[i] = Result{j.ID, err}     // disjoint indices: safe
            return err                         // first error cancels siblings
        })
    }
    err := g.Wait()                            // waits for all, even after cancel
    return results, err
}

func handle(ctx context.Context, j Job) (string, error) {
    // every blocking call takes ctx
    out, err := remote.Call(ctx, j.Payload)
    if err != nil {
        return "", fmt.Errorf("job %d: %w", j.ID, err)   // wrap with context
    }
    return out, nil
}
```

The two invariants: **each goroutine writes its own `results[i]` slot** (no
shared map, no lock), and **every blocking call receives `ctx`** so a
cancelled `Wait` actually stops work instead of waiting for it to finish.

## Semaphore: bound a dynamic stream of goroutines

```go
sem := make(chan struct{}, 8)     // at most 8 in flight
for _, item := range items {
    if err := semacquire(ctx, sem); err != nil { return err }
    g.Go(func() error {
        defer func() { <-sem }()               // release on EVERY path
        return handle(ctx, item)
    })
}
return g.Wait()
```

The `defer func(){ <-sem }()` is mandatory — a bare `<-sem` is skipped by an
early return or a panic, and the semaphore leaks until it deadlocks.

## select gotchas, collected

| Situation | Why it surprises you |
|---|---|
| Two cases ready at once | `select` picks **uniformly at random**; order in the source is irrelevant |
| Unbuffered send, reader starts later | The send case is not ready until a receiver arrives; can deadlock with no `default` |
| `case v := <-ch` on a closed channel | Yields the zero value forever — a hot spin loop unless you use `, ok` or a `ctx.Done()` case |
| `case <-ctx.Done()` on a nil ctx | `ctx.Done()` on `context.Background()` is nil → that case never fires, so a `Background()` context can never be cancelled out of this loop |
| No `default` | Blocks until some case is ready |
| `default` present | Non-blocking poll; the default branch runs when nothing is ready |
| Sending on a closed channel | `panic: send on closed channel` — only the owner closes, only after senders finish |
