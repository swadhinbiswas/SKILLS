# Memory sizing reference

How to turn "this pod gets OOMKilled" into a number. Read this when computing a
memory limit, or when reading cgroup memory events.

## What is inside a container's memory

`memory.max` (the Kubernetes limit) is the total the cgroup may use. It is not
the heap.

| Component | JVM | Node | Python | Go |
|---|---|---|---|---|
| Main heap | `-Xmx` / `MaxRAMPercentage` | V8 old space + young gen | Python objects (no cap by default) | Go heap, GC-managed |
| Metaspace / code cache | yes, outside `-Xmx` | code + JIT metadata outside old space | bytecode + `__pycache__` | in the binary |
| Thread stacks | `~1 MB × threads` | libuv threadpool | per thread | small |
| Direct/native buffers | `MaxDirectMemorySize` (defaults to `-Xmx`!) | Buffers/TypedArrays are outside the old-space limit | numpy/torch native allocations | cgo |
| Code cache / JIT | `-XX:ReservedCodeCacheSize` (240 MB default) | yes | yes | yes |
| GC structures | G1 region overhead | fragmentation | fragmentation | heap metadata |
| Page cache for files it reads | counts toward the cgroup | yes | yes | yes |
| Anything written to a tmpfs/emptyDir | **counts toward the cgroup** | yes | yes | yes |

Two consequences that surprise people:

- **`-XX:MaxDirectMemorySize` defaults to the value of `-Xmx`.** A JVM with
  `-Xmx512m` may map 512 MB of direct buffers *in addition to* the 512 MB heap.
  Set `-XX:MaxDirectMemorySize` explicitly if the app uses NIO, Netty, or
  off-heap.
- **`emptyDir` (unless `medium: Memory` with a `sizeLimit`) is on the node's
  disk, so it does not count.** `medium: Memory` puts it in a tmpfs and it
  **does** count against the container's memory. A 1 Gi `sizeLimit` on a
  memory-backed emptyDir is 1 Gi off your app's budget.

## Deriving the limit

```
limit  = heap + non_heap_overhead + peak_working_data + safety
request = limit        (for anything predictable)
```

Overhead factors to add on top of the heap:

| Runtime | Non-heap overhead to add |
|---|---|
| JVM, small heap (<512 Mi) | 30-50% (metaspace, code cache, GC, stacks) |
| JVM, 512 Mi - 2 Gi heap | 25-35% |
| JVM, >2 Gi heap | 15-25% |
| Node | 60-120 MB baseline for the runtime itself, plus ~1.5x the old-space setting for buffers/fragmentation |
| Python | 30-60 MB interpreter baseline, plus 2-3x the data you materialise if you `json.load` a whole file, plus native library allocations (numpy/torch can dwarf the Python objects) |
| Go | low, ~10-20% plus `GOMEMLIMIT` headroom; the GC is the main consumer |

Worked JVM example:

```
Peak heap needed (measured from GC logs / JFR):        700 Mi
  non-heap (metaspace + code cache + GC + stacks):    x 30% = 210 Mi
  direct buffers (NIO/Netty), set explicitly:          100 Mi
  safety margin (spikes, cache warm-up):              150 Mi
------------------------------------------------------------
limit = 700 + 210 + 100 + 150                        = 1160 Mi -> round to 1280Mi
MaxRAMPercentage = 70% of 1280Mi                    = 896 Mi heap  (>= 700 needed)
```

Express the JVM setting as a **percentage**, not a number, so the same image
works at a different limit:

```
-XX:MaxRAMPercentage=70 -XX:MaxDirectMemorySize=100m -XX:+ExitOnOutOfMemoryError
```

Then check: is `70% of limit` above the measured peak heap? If not, the limit
is too small or the peak is a leak.

Worked Node example:

```
Node baseline RSS:                                    60 MB
max-old-space-size:                                   768 MB
  heap fragmentation + external buffers:           x 1.3  = 1000 MB
  native addons / image processing:                 x 1.2  (if any)
------------------------------------------------------------
limit = 768 * 1.3 + 60                               = 1058 Mi -> 1280Mi
NODE_OPTIONS=--max-old-space-size=768
```

The trap: V8's default old-space size on a large-memory node can be several GB,
so a 1 Gi container is killed before V8's own "heap out of memory" error ever
prints. Always set `--max-old-space-size` **below** the limit.

Worked Python example:

```
interpreter + imports:                                 45 MB
peak resident data (measured RSS, not object size):   600 MB
  transient peak during a batch (e.g. list of dicts): x 1.5 on the data
------------------------------------------------------------
limit ~ 45 + 600*1.3 = 825 Mi -> 1024Mi
```

A guard for a process you control, so you get a traceback instead of a
SIGKILL:

```python
import resource
resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))   # 1 GiB address space
```

`RLIMIT_AS` limits virtual address space, which is stricter than RSS; some
allocators (notably jemalloc/tcmalloc) reserve large virtual regions and will
fail early under `RLIMIT_AS`. Prefer watching RSS and letting the cgroup handle
it if that happens.

Go:

```yaml
env: [{ name: GOMEMLIMIT, value: "900MiB" }]   # Go 1.19+, soft limit
resources: { requests: { memory: 768Mi }, limits: { memory: 1Gi } }
```

`GOMEMLIMIT` is a soft limit the GC works to respect; set it to ~90% of the
cgroup limit so the GC has headroom before the kernel kills the process.

## Reading cgroup v2 memory state

```
kubectl exec <p> -- cat /sys/fs/cgroup/memory.max        # the limit
kubectl exec <p> -- cat /sys/fs/cgroup/memory.current    # bytes in use
kubectl exec <p> -- cat /sys/fs/cgroup/memory.stat
```

`memory.stat` fields worth knowing:

| Field | Meaning |
|---|---|
| `anon` | anonymous memory: the heap. The thing you size. |
| `file` | page cache for files read; reclaimable, not a leak signal |
| `slab` | kernel allocations, including dentries and socket buffers |
| `kernel_stack`, `pagetables` | per-process and per-page kernel memory |
| `sock` | socket buffers; a leak in connection handling shows here |
| `shmem` | tmpfs, including `emptyDir` with `medium: Memory` |
| `inactive_anon`, `active_anon` | split of the heap |
| `pgfault`, `pgmajfault` | page faults; a rising `pgmajfault` means real swapping pressure |

`memory.events` (cgroup v2) is the OOM evidence:

```
low 0        # reclaimed successfully, no problem
high 0       # hit the limit and reclaimed; the app felt it
max 3        # hit the limit and could NOT reclaim -> OOM kill
oom 1        # an OOM kill happened in this cgroup
oom_kill 1   # a process was killed
```

`max` non-zero means the cgroup could not free memory in time. If `oom_kill`
is 0 but `high` is high, the app is thrashing near the limit and will be
killed on the next spike — raise the limit or reduce the working set.

## Deciding request vs limit

- **Stateless service with a predictable footprint:** `request == limit` on
  memory (and CPU, to reach `Guaranteed`). Best eviction priority, and the
  scheduler can place it accurately.
- **Bursty service:** `request` at p95, `limit` above the observed peak. The
  pod is `Burstable`; under node pressure it is evicted before `Guaranteed`
  peers, which is the correct trade.
- **Stateful data store:** `request == limit` and `Guaranteed`. It is the last
  thing you want evicted.
- **Job/batch:** a high `limit` (it can use the whole node while it runs) and a
  `request` low enough to schedule. Batch jobs do not need `Guaranteed`.

## Sanity checks

- A limit below 256 Mi is almost always wrong for a real service (a JVM, a
  Node app, or a Python app with dependencies will not boot).
- If the app is CPU-light and memory-stable, do not add a CPU limit to "be
  safe"; it introduces throttling with no benefit. Add a memory limit and a CPU
  request.
- Re-measure after every change. OOMKill is a symptom of a working set that
  changed; a limit that is correct today is wrong after the next feature.
