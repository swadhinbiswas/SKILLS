---
name: process-and-perf-cli-tooling
description: Find what is slow, stuck, or holding a resource using the OS tools directly - ps/top/htop process states, lsof and ss for ports and open files, strace basics, curl timing, iostat/iotop, and a repeatable "why is my command slow" workflow. Use when a command hangs, when a process eats CPU or memory, when "port already in use" appears, when a build is slow, or when someone asks what a process is waiting on. Triggers on "stuck process", "high CPU", "hung", "port in use", "which process", "strace", "curl timing", "slow build", "D state", "zombie".
compatibility: Linux-oriented (ps flags, /proc, ss, iotop, strace). macOS equivalents noted. Windows is out of scope.
metadata:
  version: "1.0"
---

# Process and Perf CLI Tooling

When something is slow or stuck, reach for the OS before guessing. The tools
answer four questions: **is it using CPU, is it waiting on I/O, is it waiting
on a lock/network, or is it blocked on something invisible?**

## The workflow

- [ ] 1. Is the process even running? `ps` / `pgrep` / `top`
- [ ] 2. What state is it in? `ps -o stat` and the `STAT` letter (`S`, `D`,
      `R`, `Z`, `T`) — this is the single most informative field
- [ ] 3. CPU or memory? `top`/`htop` (live), `ps` sorted (snapshot)
- [ ] 4. Blocked on a file/port? `lsof -p PID`, `lsof -i :PORT`
- [ ] 5. Blocked on a syscall? `strace -p PID` (or `cat /proc/PID/wchan`)
- [ ] 6. Network: `ss -tlnp`, `curl -w` timing, `dig`/`nslookup`
- [ ] 7. Disk: `iostat -x 1`, `iotop -oPa`, `du`/`df`
- [ ] 8. Long-running work: put it in `tmux` so it survives your SSH session

## `ps` and process states

`ps aux` is the snapshot; `top`/`htop` is live. Make `ps` do the sorting for
you instead of piping to `sort`:

```bash
ps -eo pid,ppid,stat,etime,%cpu,%mem,args --sort=-%cpu | head -15
ps -eo pid,stat,%mem,rss,args --sort=-rss | head            # memory hogs
ps -eo pid,stat,etime,args --sort=etime  | head             # oldest first
pgrep -af 'node|webpack'                                    # find by pattern (-a = full cmdline)
ps -o pid,ppid,stat,wchan:20,args -p 1234                   # what is it waiting on
pstree -p 1234                                              # the process tree under 1234
```

On Linux, `STAT` (process state) is a letter plus modifiers. The base letter:

| Letter | State | Meaning | What to do |
|---|---|---|---|
| `R` | Running (or runnable) | using CPU, or waiting for CPU | fine; high sustained `R` = CPU-bound |
| `S` | Sleeping | waiting for an event (I/O, timer, network) | normal for most daemons; a long `S` on a network wait is interesting |
| `D` | **Uninterruptible sleep** | blocked in the kernel on I/O (disk/NFS) | **not** killable with `-9`; you must fix the I/O. This is the "hung" state |
| `Z` | Zombie | exited, parent hasn't reaped it | parent is broken; grows unbounded; restart the parent |
| `T` | Stopped | suspended by `SIGSTOP` or a debugger | `kill -CONT PID` to resume |
| `I` | Idle | idle kernel thread (not your process) | ignore |

Modifiers: `s` = session leader, `+` = foreground, `l` = multi-threaded, `N`/
`L` = low/normal priority, `<` = high priority, `R` = `SCHED_FIFO/RR` real-time.

The ones that matter: **`D` = stuck on I/O (uninterruptible, `kill -9` won't
work)**, **`Z` = zombie (parent not reaping)**, **`T` = stopped**. You can read
the base letter straight from `/proc/PID/stat` field 3, and the kernel
function it's blocked in from `/proc/PID/wchan` (Linux):

```bash
awk '{print $3}' /proc/1234/stat        # state letter
cat /proc/1234/wchan; echo              # e.g. do_wait, nfs_wait_bit_kq
cat /proc/1234/stack                    # kernel stack, needs root
```

`top` vs `htop`: `htop` adds per-thread view, tree, and colour. `top` is
always present; in `top`, press `1` for per-CPU, `M`/`P` to sort by mem/cpu,
`c` for full command. In `htop`, `F2`/F9 setup, `H` toggles threads. To
capture `top` output for a script, `top -b -n1 -o %CPU | head` (batch mode).

`%CPU` in `ps`/`top` is *average over process lifetime*, not instantaneous —
a process that was idle for an hour and busy for one second shows ~0. For
instantaneous, sample twice or use `pidstat`/`top -b -n2 -d 1`.

## "Which process has this port / this file?"

```bash
lsof -i :8080                       # process listening/using TCP 8080
lsof -i :8080 -sTCP:LISTEN          # just listeners
lsof -p 1234                        # every fd a process holds
lsof -p 1234 | grep REG             # only open files (not sockets/pipes)
lsof +D /var/log                    # who has files open under a dir
lsof -u $USER                       # everything owned by a user
kill -9 $(lsof -t -i :8080)         # kill whatever holds the port (see caveat)
```

`ss` is the modern, faster replacement for `netstat` for sockets:

```bash
ss -tlnp                            # TCP listeners + owning process
ss -ulnp                            # UDP listeners
ss -tanp | head                     # all TCP connections
ss -tn state established '( dport = :443 or sport = :443 )'
ss -ti dst 10.0.0.5                 # per-connection stats (rtt, retransmits)
```

`ss -i` is the underrated one: `retrans:X/Y` and high `rtt` reveal packet
loss or a bad path. If `ss` shows a socket in `SYN-SENT` that never leaves,
it's a firewall dropping, not refusing.

Caveat on `kill $(lsof -t ...)`: it can kill a process you did not intend if
the port is in `TIME_WAIT` (no process) or shared (a container's
namespace). Prefer `fuser -k 8080/tcp` for a "this is my dev server" case, or
find the PID and kill it deliberately.

## Blocking on a syscall: `strace` and friends

`strace` shows every syscall a process makes — the ground truth for "what is
it waiting on". It needs `strace` installed (`apt install strace` /
`apk add strace`) and, to attach to another user's process, root.

```bash
strace -p 1234                     # attach and follow (Ctrl-C to stop)
strace -f -p 1234                  # follow forks/threads too
strace -T -p 1234                  # print time spent in each syscall
strace -e trace=openat,read,write -p 1234    # only file I/O
strace -c -o /tmp/st.txt sleep 10  # count syscalls, summarise, run a command
```

What you see and what it means:

- Repeated `poll`/`select`/`epoll_wait`/`futex` — the process is idle,
  waiting for a socket or another thread. It is *not* busy; the slowness is
  upstream (the thing it is waiting on).
- `openat` on a path that returns `ENOENT`/`EACCES` in a loop — a missing
  file or a permissions problem, retried forever.
- `connect` to a port that never completes — firewall or a dead endpoint.
- Long gaps between syscalls with the process in `D` — disk/NFS latency.
- `futex` with a lot of `FUTEX_WAIT` — thread contention (a lock or a
  thread pool starving).

Alternatives when you cannot `strace` (no root, container): `/proc/PID/stack`
(kernel stack, root), `/proc/PID/wchan` (blocked function), `py-spy`/`py-spy
dump` for Python, `gdb -p PID` thread apply `bt` for a native stack, and
language-native profilers (flame graphs). For "stuck" specifically,
`/proc/PID/wchan` + `ps` state (`D` vs `S`) usually tells you enough without
strace at all.

## Is it the network? Time it

```bash
curl -s -o /dev/null -w 'dns=%{time_namelookup} conn=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} code=%{http_code}\n' https://api.example.com/x
```

Read the fields left to right — the gap tells you *where* the time went:

- `time_namelookup` high → DNS. `dig host` / `dig +short host`; compare
  against a public resolver.
- `time_connect` high → network RTT or a firewall.
- `time_appconnect` − `time_connect` high → TLS handshake (cipher, cert
  chain, or a middlebox).
- `time_starttransfer` − `time_appconnect` high → **server think time**. This
  is the one that means "the API is slow", and it is a server problem, not a
  network one.
- `time_total` high but `code=000` → connection never completed.
- Repeat with `-o /dev/null` so the body download doesn't mask TTFB.

Related: `ping`, `mtr host` (which hop is lossy), `curl -I` (headers only),
`dig` (DNS), `nc -zv host port` (is the port open), `openssl s_client -connect
host:443` (TLS inspection). HTTP-specific timing lives in the
`observability` domain.

## Is it the disk?

```bash
df -h                    # space, and % full
df -i                    # **inode** exhaustion — df -h can be 0% and writes still fail
du -sh /var/lib/* | sort -h | tail      # what's big (careful: slow)
du -x -h --max-depth=2 / 2>/dev/null | sort -h | tail -20
iostat -x 1              # per-device: %util, await, r/s w/s — run for a few seconds
iotop -oPa               # per-process disk I/O (needs root; -o only active, -P pid, -a accum)
vmstat 1                 # r=runqueue, b=blocked, wa=iowait, si/so=swap
```

Interpretation: `%util` near 100 with high `await` = saturated device.
High `wa` (iowait) in `vmstat`/`top` = processes are waiting on disk (expect
many `D` states). High `si`/`so` = swapping — you are out of RAM, and
everything gets slow. `df -i` full with space free is a classic "disk full"
report that is actually an inode leak (millions of tiny files).

## Long-running work: `tmux` / `screen`

Anything that must outlive your SSH session belongs in `tmux`. A plain
background `&` job dies when the SSH connection drops; `tmux` keeps it, and
lets you detach/reattach from anywhere.

```bash
tmux new -s build                 # new named session
tmux new -s x -d './long_job.sh'  # start detached
tmux ls                            # list sessions
tmux attach -t build              # reattach
# Ctrl-b d  -> detach (leaves it running)
# inside:  Ctrl-b c  new window, Ctrl-b n/p next/prev, Ctrl-b %  split panes, Ctrl-b [  copy mode
tmux kill-session -t build
```

`screen` is the older equivalent: `screen -S build`, `Ctrl-a d` to detach,
`screen -ls`. Use `tmux` (split panes, scripting, better defaults).

Why it matters beyond persistence: a `tmux` pane is a **real terminal with a
TTY**, so interactive prompts, progress bars, and `top` work, and output is
scrollable after the fact (`Ctrl-b [` then `/` to search). Log to a file
*in addition* if you need the output after the session dies.

`nohup cmd &` and `setsid cmd &` also survive disconnect (no TTY → no
progress bars, output must be redirected). `disown` after `&` protects from
`SIGHUP` in the current shell only.

## "Why is my command slow" — the repeatable path

1. **Reproduce and time it.** `time cmd` (bash builtin) for user/sys/real. If
   `user` ≈ `real`, it's CPU-bound; if `real` ≫ `user+sys`, it's waiting (I/O,
   network, lock). `/usr/bin/time -v` (GNU coreutils) adds max RSS, page
   faults, and context switches; `time` builtin does not.
2. **Is it CPU?** `top` sorted by `%CPU`, or `pidstat -u 1 <pid>`. If the
   process is at 100% of one core for the whole run, it is genuinely
   computing — profile it (language profiler), don't `strace` it.
3. **Is it I/O wait?** `iostat -x 1` during the run; `vmstat 1` (high `wa`).
   Many `D` states → disk. Then ask what I/O: `iotop -oPa` (which process),
   or `lsof +D` / `du` (which files).
4. **Is it network?** `curl -w` (above) for one call; `ss -ti` for connection
   health; `mtr` for the path.
5. **Is it blocked on something else?** `ps -o stat,wchan`; if `S` on a socket
   or a futex, the real work is in *another* process — find it with
   `pstree -p PID` (a child) or `ss -tnp` (a peer). A `D` on `nfs_*` is a
   hung network filesystem; `cat /proc/PID/wchan` says `nfs_wait_bit_kq`.
6. **Is it the shell, not the program?** `set -x` to see what the script is
   really doing (see `shell-scripting-robustness` in the `terminal` domain);
   `time` each stage.

Context switches and waits: `pidstat -w 1` (voluntary/involuntary ctx
switches), `pidstat -d 1` (I/O), `pidstat -r 1` (memory). Involuntary context
switches at a high rate = CPU thrash (too many runnable processes).

## Gotchas

- **`kill -9` cannot kill a `D`-state process** — it is uninterruptible in the
  kernel. Fix the underlying I/O (the NFS mount, the dead disk) and it
  returns. `kill -9` on a `T` process does nothing; use `kill -CONT`.
- **`kill -9` does not clean up.** The process cannot flush, release locks, or
  close sockets. Prefer `SIGTERM`, wait, then `SIGKILL`:
  `kill -TERM $PID; for i in {1..10}; do kill -0 $PID 2>/dev/null || break; sleep 1; done; kill -9 $PID 2>/dev/null`.
- **`%CPU` in `ps` is lifetime-average**, not current. Use `top`/`pidstat` for
  instantaneous. A 5-second burst barely moves `ps`'s `%CPU`.
- **`lsof` without `-n`/`-P` is slow** (DNS lookups). Use `lsof -nP` for speed
  and raw IPs/ports.
- **`lsof` may show nothing for a port owned by a container** — the port is in
  the container's net namespace. Use `ss` inside the container or
  `docker port`.
- **`kill -0 PID`** tests existence (no signal sent) — the safe liveness check
  in scripts, and it works for processes you don't own.
- **`netstat` is deprecated and slow**; use `ss`. `netstat -tulpn` output is
  still found in old runbooks but `ss -tulpn` is the same info, faster.
- **`top` in a container shows host state, not the container's**, unless you
  use cgroup-aware tools — a container's "memory leak" is often the host's.
- **`strace` is very slow** (every syscall trapped). Never `strace` a
  performance-critical process expecting fast output; use it to find a *block*,
  not to profile. Attach with `-p`, observe briefly, detach. `-c` for a
  summary count. It is also often blocked by security policy (no ptrace) and
  unavailable without root in containers.
- **A zombie cannot be killed** — it is already dead. It goes away when the
  *parent* reaps it (waits) or exits. Find the parent with `ps -o ppid`.
- **Environment changes don't affect running processes.** A `tmux` pane or
  process started before you changed `PATH`/env keeps the old environment
  until restarted.
- **`free` output units vary** (`-h` for human, `-m` for MB). `available`, not
  `free`, is the number that matters.

## Checklist

- [ ] Reproduced and timed it (`time cmd`); `user` vs `real` says CPU vs
      wait.
- [ ] Checked process state: `D` (I/O, unkillable), `Z` (zombie, fix parent),
      `T` (stopped), high `R` (CPU-bound).
- [ ] If blocked: `lsof -nP -p PID` / `ss -tlnp` / `cat /proc/PID/wchan`.
- [ ] If network: `curl -w` — TTFB is the server, `connect`/`dns` is the path.
- [ ] If disk: `iostat -x 1`, `vmstat 1` (wa/swap), `df -i` for inodes.
- [ ] Anything long-running is in `tmux` (or `nohup`/`setsid`) so it survives
      disconnect.
- [ ] `kill -TERM` before `kill -9`; never expect `-9` to beat `D` state.
- [ ] Read `references/perf-tooling-matrix.md` for a tool-by-tool decision
      table (which tool answers CPU vs memory vs I/O vs network vs lock, and
      the exact flags on Linux and macOS).
