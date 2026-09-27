---
name: nodejs-server-performance
description: Keep a Node.js server fast and alive - what blocks the event loop, streaming vs buffering large payloads, worker_threads vs cluster vs child_process, the libuv threadpool that crypto and fs saturate, graceful shutdown on SIGTERM, and the exact unhandledRejection/uncaughtException semantics. Use when a Node service is slow under load, when CPU work blocks requests, when p99 spikes, when the process dies on an error, or when someone asks about scaling Node across cores. Triggers on "event loop blocked", "event loop lag", "worker_threads", "cluster", "UV_THREADPOOL_SIZE", "SIGTERM", "unhandledRejection", "streaming", "memory leak".
compatibility: Node 20+ (LTS). Express 4/5, Fastify. Node 18+ has fetch; 20+ has the stable test runner. Verify flags with `node --help`.
metadata:
  version: "1.0"
---

# Node.js Server Performance

Node is fast because one thread does not block. Every performance bug in a Node
server is either **something blocking that thread** or **work that never
scales past one core**. The event loop is the whole architecture: if it is
busy, the process is down regardless of how much CPU the box has.

## Workflow

- [ ] 1. Measure **event loop lag** and p99 latency, not CPU% — lag is the
      signal that matters
- [ ] 2. Find the blocking call (the libuv threadpool, or sync CPU on the main
      thread)
- [ ] 3. Decide: move it off-thread (`worker_threads`/async API), or scale
      across cores (cluster/one-process-per-core)
- [ ] 4. Stream large payloads; never buffer them whole
- [ ] 5. Handle `SIGTERM` gracefully: stop accepting, drain, close, exit
- [ ] 6. Install `unhandledRejection`/`uncaughtException` handlers that log
      and exit deliberately, not silently continue

## What blocks the event loop

The event loop is a single thread. While a JavaScript callback runs, **no
other request is served**. The culprits, in order of how often they are the
answer:

1. **Synchronous filesystem** — `fs.readFileSync`, `fs.writeFileSync`,
   `existsSync`, `readdirSync`. On a network filesystem or a large file this
   is tens of milliseconds of total stall.
2. **Synchronous crypto** — `crypto.hashSync` (Node 21.7+/22+),
   `pbkdf2Sync`, `randomBytes` for large buffers.
3. **CPU-bound JS** — JSON parse/stringify of a huge payload, a big regex, a
   tight loop, compression (`zlib` sync), sorting a large array.
4. **A blocking call in a dependency** — a template engine rendering
   synchronously, a JWT library, a synchronous DB driver.
5. **A synchronous child process** — `execSync`, `spawnSync`.

```js
// Wrong: every other request waits for this.
const data = fs.readFileSync(path);          // stalls the loop
const hash = crypto.pbkdf2Sync(pw, salt, 100000, 32, 'sha256');   // stalls badly

// Right: the loop stays free; these hand work to the libuv threadpool.
const data = await fs.promises.readFile(path);
const hash = crypto.pbkdf2(pw, salt, 100000, 32, 'sha256');      // callback -> Promise
```

- **The libuv threadpool** is a small pool (default **4 threads**) that
  handles "async but actually blocking" work: most `fs` operations, DNS
  lookup (`dns.lookup`), and `crypto` such as `pbkdf2`, `randomBytes`,
  `scrypt`, `createHash` (for large inputs). It is why `await` on these does
  not block the loop — and why they can still saturate.
- **Saturating the threadpool** stalls every operation that uses it, even
  though the loop is "free". If 4 concurrent `fs.readFile` calls are slow, the
  other 100 wait their turn. `UV_THREADPOOL_SIZE=16 node app.js` (must be set
  **before** the process starts; it is read at startup) raises it. This is the
  single highest-leverage knob for fs/crypto-heavy services, and it is a
  blunt instrument — measure.
- **Diagnose lag, not CPU.** CPU% can be 15% while the loop is stalled,
  because the "work" is a blocking call that shows as low overall CPU. Measure
  event loop delay:

```js
// A timer that should fire every 100ms; lag = delay - 100ms.
let last = process.hrtime.bigint();
setInterval(() => {
  const now = process.hrtime.bigint();
  const lagMs = Number(now - last) / 1e6 - 100;
  if (lagMs > 100) console.warn(`event loop lag ${lagMs.toFixed(1)}ms`);
  last = now;
}, 100).unref();
```

  Or use `perf_hooks.monitorEventLoopDelay()` for a histogram, or the
  `eventloop-delay-monitor` / `clinic doctor` tooling. **p99 latency tracks
  event loop lag directly** — every request in flight waits for the loop.

- In general, if p99 is 10x p50 and the extra time is not downstream I/O,
  look for a blocking call. If CPU is pegged at 100% on one core, it is CPU
  work on the main thread (move it to a worker or across processes).

## Streaming vs buffering

Buffering a large payload means holding it entirely in memory, then copying it
to another buffer to write — twice the memory, and a spike as concurrent
requests pile up. **Stream when the size is unbounded or user-controlled.**

```js
// Buffers the whole file: 200MB resident, times concurrent requests. OOM risk.
res.end(await fs.promises.readFile(bigPath));

// Streams: constant memory, starts sending immediately.
createReadStream(bigPath).pipe(res);

// HTTP download straight through, no disk touch at all.
const upstream = await fetch(url);
Readable.fromWeb(upstream.body).pipe(res);   // Node 18+; backpressure propagates
```

- **Request bodies:** `express.json()` buffers the whole body into memory and
  rejects over `limit` (default 100kb) with
  `PayloadTooLargeError: request entity too large`. For big uploads, stream
  with `busboy` or pass the raw stream to the handler.
- **Responses:** `res.json(obj)` serialises the whole object to a string in
  memory first. For a large list, write chunks and let backpressure work. If
  the data starts in a database, use a **cursor/paginate** (never load 1M rows
  to filter to 10). See `api-design/pagination-and-filtering`.
- **Backpressure is the point of streaming.** If you build a readable from an
  array and `pipe` it, memory still grows; respect `write()` returning false
  and pause. `pipeline()` (from `stream/promises`) propagates backpressure and
  error handling correctly and is the right tool over raw `pipe`.

## Scaling across cores: workers, cluster, or containers

Node runs JS on one thread per process. To use more than one core you need
more **processes** (or threads for CPU work). Choose by role:

| Tool | What it is | Use for |
|---|---|---|
| `worker_threads` | Threads in **one process**, separate V8 isolates and heaps | CPU-bound work inside one service; heavy lib that blocks; keeps shared memory possible |
| `cluster` | Master forks N worker **processes**, round-robins connections | Scaling a stateless HTTP service on one box; the master owns the listening socket |
| `child_process` | Separate OS **process** | Running a different binary (ffmpeg, a shell), isolation, or something crash-prone |
| Multiple containers/PM2 | Multiple processes, often multiple machines | The production default for stateless services |

```js
import cluster from 'node:cluster';
import { availableParallelism } from 'node:os';

if (cluster.isPrimary) {
  for (let i = 0; i < availableParallelism(); i++) cluster.fork();  // 'isPrimary' replaces 'isMaster' (Node 16+)
  cluster.on('exit', () => cluster.fork());                          // respawn a dead worker
} else {
  startServer();
}
```

- `cluster.isPrimary` replaced `isMaster` in Node 16; both work, use
  `isPrimary`. The number of workers: `availableParallelism()` (Node 18.14+)
  or `os.cpus().length`. In containers, `availableParallelism()` respects
  cgroup CPU limits better than `cpus().length` (which sees the host) — this
  is why cluster-per-container often over-forks and tanks throughput.
- **Prefer one process per container in production**, orchestrated by
  Kubernetes with HPA, over `cluster` in one box. You lose nothing and you
  gain real horizontal scaling and a clean SIGTERM story.
- `cluster` round-robins new connections, but a `SIGTERM` must reach every
  worker — see graceful shutdown below.
- **`worker_threads` are not free.** Spawn cost is milliseconds, each has its
  own heap, and structured-clone messages cost (transferables — `ArrayBuffer`,
  `MessagePort` — avoid the copy for big data). Use them for genuinely CPU-heavy
  units of work; not to "add concurrency" for I/O (the loop already does
  that).
- **Never `child_process.exec` per request.** Spawning a shell per request is
  tens of milliseconds of CPU and a process leak risk. Spawn once, reuse, or
  use a long-lived subprocess protocol.

## Graceful shutdown on SIGTERM

Containers and orchestrators send **SIGTERM** and wait a grace period, then
SIGKILL. On SIGKILL, in-flight requests die and clients see resets. Handle
SIGTERM: stop accepting new work, finish or fail what is in flight, close, exit.

```js
let shuttingDown = false;

async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`${signal} received, draining...`);

  server.close(async () => {                 // stops accepting NEW connections
    try {
      await db.end();                        // flush/close pools
      await redis.quit();
      console.log('clean shutdown');
      process.exit(0);
    } catch (err) {
      console.error('shutdown error', err);
      process.exit(1);
    }
  });

  // Don't hang forever: force-exit if drain takes too long.
  setTimeout(() => { console.error('forced exit'); process.exit(1); }, 25_000).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));
```

- `server.close()` stops **new** connections but existing keep-alive
  connections stay open; with Node 19+ `server.close()` also **closes idle
  keep-alive connections** and waits for active ones. On older Node, call
  `server.closeIdleConnections()` if available. This version difference is why
  "my graceful shutdown hangs" happens — a keep-alive socket that never
  closes keeps the process alive.
- Track in-flight requests (a counter incremented on request start, decremented
  on `res.on('finish')`) and wait for the counter to hit zero before closing
  the DB, if you must complete every request.
- Set the platform grace period longer than your drain timeout: Kubernetes
  `terminationGracePeriodSeconds` (default **30**) must exceed the time your
  handler needs. If your handler force-exits at 25s, you have 5s of margin —
  fine; if it force-exits at 40s, you get SIGKILLed at 30s first.
- In `cluster`, forward the signal to workers, or the master exits and the
  workers are orphaned/reaped mid-request.
- `.unref()` on your drain-timeout timer so it does not itself keep the loop
  alive.

## Unhandled rejection / uncaught exception — the exact semantics

Node's defaults changed in v15 and the difference matters:

- **`uncaughtException`** (a throw that escapes all handlers, a failed
  `require` at load, a bug in a callback): in v15+ the process **exits** by
  default. Catching it and continuing is **unsound** — the process is in an
  unknown state (a half-written file, a corrupt cache, a lock held). The right
  pattern is: log it (synchronously, to a place that survives the exit), then
  `process.exit(1)` and let the supervisor (PM2, Docker restart policy, K8s)
  replace the process. **Do not keep running.**
- **`unhandledRejection`** (a `Promise` rejected with no `.catch`): in v15+ the
  default is also to **exit** (the `--unhandled-rejections` flag defaults to
  `throw`, which becomes an uncaught exception). Catching it and continuing is
  also generally unsound, because a rejected promise often means a write, a
  transaction, or an emit silently did not happen.

```js
process.on('unhandledRejection', (reason) => {
  console.error('unhandledRejection', reason);   // log first
  process.exitCode = 1;                          // then let it die cleanly
});
process.on('uncaughtException', (err) => {
  console.error('uncaughtException', err);       // sync log (stderr survives)
  process.exit(1);                                // supervisor restarts
});
```

- The asymmetry to internalise: catching these and calling
  `console.error` and then **continuing** is the common and dangerous habit.
  A global handler should be the **last** step before a clean exit, plus a
  final synchronous flush of logs. The supervisor restarts you; you lose a few
  in-flight requests; you do not serve corrupted state forever.
- `process.exitCode = 1` + letting the loop drain is **better than
  `process.exit(1)`** when you have graceful shutdown: it lets open handles
  close and logs flush, then exits 1. `process.exit()` truncates async stdout
  writes. Combine: set `exitCode`, run your `shutdown()`, and in its callback
  fall off the end.
- A rejected promise in a library you call is *your* responsibility if you did
  not await it — fix the call site, not just the global handler.
- `process.on('warning')` catches deprecation/performance warnings (e.g.
  `MaxListenersExceededWarning`, which means a leak of listeners on an
  emitter); log it.

## Gotchas

- **CPU% on one core = you are single-threaded-limited.** Node does not use
  other cores by itself. If CPU is pegged, move CPU work to `worker_threads` or
  scale processes.
- **Low CPU + high latency = you are blocked, not busy.** Look for sync
  `fs`/`crypto`/`exec` or a slow dependency, not for "not enough CPU".
- **`JSON.parse` of a multi-megabyte string is a blocking, one-shot
  pause.** If a client can send a huge body, enforce a size limit
  (`express.json({ limit: '100kb' })`) *and* reject oversized with 413.
- **The libuv threadpool default is 4.** `UV_THREADPOOL_SIZE` is read once at
  startup — setting `process.env.UV_THREADPOOL_SIZE` in code does nothing;
  export it before `node`. It applies to `fs`, `dns.lookup`, and much of
  `crypto`, which is why "async fs is mysteriously slow under load".
- **`dns.lookup` uses the threadpool; `dns.resolve` does not.** For high-volume
  outbound lookups, `dns.resolve4`/`resolveAny` avoid the pool.
- **`forEach` with an `async` callback does not await** — the loop finishes
  before the promises do, and errors become unhandled rejections. Use
  `for...of` with `await`, or `Promise.all(items.map(...))`.
- **A `Buffer` is not a `string`.** Mixing them in `crypto`, `Buffer.from`
  vs `Buffer.alloc`, and comparing `buf === str` (always false) are frequent
  correctness bugs, not just perf ones.
- **`require` vs `import`**: ESM is async under the hood; a top-level
  `await` in ESM can delay startup. For CLI startup time, prefer `require` of
  the heavy deps lazily, or measure. Do not `import` a 200MB library at the
  top of a small CLI.
- **Memory: a `Buffer`/`string` over ~512MB risks V8's old-space limits and a
  process abort.** Stream instead of buffering large bodies.
- **`--max-old-space-size` and clustering trade off:** more heap per process
  means fewer processes fit in RAM. Decide which you are short on.
- **Express 5 is out** (v5 changed error handling to auto-catch rejected
  promises from handlers; Express 4 does not — an `async` handler that throws
  in Express 4 results in an unhandled rejection, not a 500). Check your
  Express major; this is a real source of "my endpoint hangs instead of
  returning 500".

## Checklist for a new Node service

- [ ] No `*Sync` in request paths; no blocking dependency calls
- [ ] `UV_THREADPOOL_SIZE` set (before start) if fs/crypto heavy
- [ ] Large payloads streamed with backpressure (`stream/promises.pipeline`)
- [ ] Request body size limited, `413` on overflow
- [ ] Scaling via processes (containers preferred; `cluster` or
      `availableParallelism()` workers)
- [ ] `SIGTERM` handler drains and exits within the platform grace period
- [ ] `unhandledRejection`/`uncaughtException` log then exit; supervisor
      restarts
- [ ] Event loop lag measured in production, alerted on
