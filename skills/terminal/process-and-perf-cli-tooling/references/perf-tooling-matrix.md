# Performance tooling matrix

Which tool answers which question, with exact invocations. Linux first; macOS
differences noted. Windows is out of scope.

## Which tool answers which question

| Question | Linux tool | macOS equivalent |
|---|---|---|
| Is the process running / what is it | `ps`, `pgrep -af`, `pstree -p` | same |
| Live CPU/mem per process | `top`, `htop`, `pidstat` | `top` |
| Process state (R/S/D/Z/T) | `ps -o stat`, `/proc/PID/stat` | `ps -o stat` |
| What is it blocked on | `cat /proc/PID/wchan`, `strace -p` | `sample PID` |
| Per-process CPU over time | `pidstat -u 1 <pid>` | `top -pid` |
| Memory (RSS) hogs | `ps --sort=-rss`, `pmap`, `/proc/PID/smaps_rollup` | `vmmap` |
| What files does it hold open | `lsof -nP -p PID` | `lsof -nP -p PID` |
| Who holds this port | `ss -tlnp`, `lsof -i :PORT` | `lsof -nP -i :PORT` |
| Socket health (rtt, loss) | `ss -ti` | `netstat -s`, `nettop` |
| Disk usage by device | `iostat -x 1`, `iotop -oPa` | `iostat -w 1`, `fs_usage` |
| System CPU/mem/swap/io | `vmstat 1`, `free -h`, `uptime` | `vm_stat`, `top` |
| System calls | `strace -p`, `bpftrace` | `dtruss` (root) |
| Syscall counts (summary) | `strace -c`, `perf trace` | `dtruss` |
| CPU profiling (native) | `perf record` + `perf report` | `sample`/`spindump` |
| Python profiling | `py-spy`, `cProfile` | same |
| Network one-shot timing | `curl -w` | same |
| DNS timing | `dig`, `kdig` | `dig` |
| Path MTU/loss | `mtr`, `tracepath` | `traceroute` |
| Long jobs that survive SSH | `tmux`, `nohup`, `setsid` | same |

## ps flags that matter (Linux / procps)

```bash
ps -eo pid,ppid,stat,etime,%cpu,%mem,rss,args --sort=-%cpu
ps -ef --forest                 # tree
ps -eLf                         # one line per THREAD
ps --ppid 1234                  # children of 1234
ps -o pid,stat,wchan:30,args -p 1234
ps -p $(pgrep -d, -f 'node')    # pgrep -d, joins with commas for -p
ps -o etimes= -p 1234           # elapsed seconds (numeric, for scripts)
```

macOS `ps` differs: `ps aux`, `ps -o pid,ppid,state,%cpu,%mem,command`, and
no `--sort`; use `ps aux | sort -k3 -nr | head` instead. `state` (not
`stat`) is the macOS column name.

## pidstat (sysstat) — the numbers `ps` cannot give

`pidstat` reports *intervals*, not lifetime averages. This is why it beats
`ps` for "is this process busy right now".

```bash
pidstat -u -r -d -w -p ALL 1 5    # cpu, mem, io, ctx switches, 1s x 5 samples
pidstat -u 1 -p 1234
pidstat -d -p ALL 1                # disk I/O per process
pidstat -r -p ALL 1                # memory/faults
pidstat -w -p ALL 1                # context switches
pidstat -l                         # system-wide, per-CPU
```

Key fields: `%usr %sys %CPU`, `kB_rd/s kB_wr/s`, `minor major` faults,
`cswch/s` (context switches), `invcswch/s` (involuntary — CPU thrash when
high), `runnable/s` (runqueue length — CPU oversubscription when > #cores).

## iostat / vmstat / free

```bash
iostat -x 1 5        # per-device: %util await r/s w/s rkB/s wkB/s, 1s x5
iostat -c 1          # just CPU
vmstat 1 5           # r (runqueue) b (blocked) wa (iowait) si/so (swap) id
free -h              # total/used/free/available/buff/cache
uptime               # load average (1/5/15 min); > core count = oversubscribed
```

`iostat -x` interpretation: `%util` near 100 = device saturated;
`await` (ms) high = requests queue. Modern NVMe/SSD: high util with low
await is fine and normal. `vmstat`: `wa` > 10 sustained with `b` > 0 = I/O
bound. `si`/`so` > 0 = swapping; the fix is memory, not disk.

## lsof and ss, precisely

```bash
lsof -nP -i :8080            # -n no DNS, -P no port-name resolution (SPEED)
lsof -nP -i TCP:8080 -sTCP:LISTEN
lsof -nP -p 1234
lsof -nP -p 1234 -a -d cwd   # just the cwd of a process
lsof -nP -u 1000
lsof -nP +D /var/lib/postgresql   # processes with files open under a dir (SLOW, recursive)
lsof -nP -t -i :8080 | xargs -r kill  # PIDs on a port, kill them

ss -tlnp                    # TCP listeners, with process
ss -ulnp                    # UDP listeners
ss -tanp | head -40
ss -tn state established
ss -s                       # socket summary counters
ss -ti dst 10.0.0.5         # per-conn: rtt, retrans, cwnd, delivery_rate
ss -lnt                     # listeners only, no process (works unprivileged w/o ptrace)
```

`ss -i` fields worth reading: `rtt`/`rttvar` (latency), `retrans:X/Y`
(retransmit ratio — loss), `cwnd` (congestion window — small = loss/pain),
`sndbuf`/`rcvbuf` (buffer sizes).

## curl timing fields (copy this)

```bash
curl -sS -o /dev/null -w \
'dns=%{time_namelookup}s tcp=%{time_connect}s tls=%{time_appconnect}s ttfb=%{time_starttransfer}s total=%{time_total}s code=%{http_code} bytes=%{size_download}\n' \
  https://api.example.com/path
```

Additional useful `-w` variables: `%{time_redirect}`, `%{num_connects}`,
`%{speed_download}`, `%{http_version}`, `%{ssl_verify_result}` (0 = OK),
`%{remote_ip}`.

```bash
# repeat N times, show each (find the tail, not the average)
for i in $(seq 1 20); do curl -sS -o /dev/null -w "%{time_total} %{http_code}\n" "$URL"; done | sort -n
```

Gap analysis (as in SKILL.md): ttfb−tls = server think time. total−ttfb =
download. dns/tcp/tls = path cost.

## strace patterns

```bash
strace -p 1234                          # attach, follow
strace -f -p 1234                       # + threads/children
strace -T -p 1234                       # time in each syscall
strace -e trace=%file -p 1234           # file syscalls
strace -e trace=%network -p 1234        # network syscalls
strace -c -o /tmp/s.txt -- sleep 5      # summary counts of a command
strace -yy -p 1234                      # annotate fds with what they point to (-yy is very useful)
strace -yy -e trace=openat -p 1234      # "which file is it stuck opening"
```

- Output meanings: a syscall with a large gap and no return = currently
  blocked in it. `= -1 ENOENT` = failed (missing file). `= -1 ETIMEDOUT` =
  network timeout. `futex(... FUTEX_WAIT ...)` = waiting on a lock/thread.
- `-c` summary: high `openat` count = churn; high `read` on one fd = streaming;
  high `futex` = contention; thousands of `stat` = a slow filesystem.
- `strace` needs `ptrace` capability; in containers it is often blocked
  (`--cap-add=SYS_PTRACE`, or `--security-opt seccomp=unconfined`).
- `strace -yy` resolving fd → path/address is usually the single highest-value
  flag for "what is it doing".

## Per-PID live view without strace

```bash
cat /proc/1234/wchan; echo          # kernel function it's blocked in
cat /proc/1234/status | grep -E 'State|Threads|VmRSS'
cat /proc/1234/stack                # kernel stack (root)
cat /proc/1234/io                    # read_bytes/write_bytes counters
cat /proc/1234/limits                # RLIMIT_NOFILE etc ("too many open files" = 1024)
ls -l /proc/1234/fd | head          # what each fd points at
for t in /proc/1234/task/*; do echo "$t: $(cat $t/stat | awk '{print $3}') $(cat $t/wchan 2>/dev/null)"; done
```

`VmRSS` is real resident memory; `Threads` count vs `ps -eLf` line count tells
you about a thread leak. `max open files` from `/proc/PID/limits` explains
"Too many open files" — raise it with `ulimit -n` (and the service's
`LimitNOFILE`).

## CPU profilers (when it is genuinely CPU-bound)

```bash
perf record -F 99 -g -p 1234 -- sleep 10 && perf report   # Linux, native
perf top -p 1234                                        # live, no record
py-spy dump --pid 1234                                  # Python, no instrumentation
py-spy top --pid 1234
node --prof app.js && node --prof-process isolate-*.log # Node
go tool pprof http://localhost:6060/debug/pprof/profile # Go
```

`perf` needs `linux-tools` and usually `perf_event_paranoid` lowered.
`py-spy` and Node/Go profilers work **without** restarting the process
(`py-spy dump`) or with low overhead — prefer them for a running production
service.

## macOS differences (short)

- Process: `top -o cpu` (sort), `ps aux`, `sample <pid> 5` (poor man's
  strace), `vmmap <pid>` (memory), `leaks <pid>`.
- Network: `lsof -nP -i`, `nettop` (per-process bytes), `netstat -an`.
- Disk: `iostat -w 1 -c 5`, `fs_usage` (needs root), `dtrash`.
- System calls: `sudo dtruss -p <pid>` (macOS strace equivalent).
- Load: `vm_stat`, `sysctl vm.loadavg`, `memory_pressure`.
- `top` on macOS shows a different set of columns and no per-thread by
  default; use `top -pid <pid>` for one process.

## Decision flow (what do I run first?)

1. `time cmd` → `user` vs `real`. CPU-bound vs waiting.
2. Waiting →
   - `ps -o stat,wchan -p PID` → `D` (disk: `iostat -x 1`, `iotop -oPa`) /
     `S`+socket (`ss -ti`, peer process) / `S`+futex (thread contention:
     `pidstat -w 1`).
   - Network suspicion → `curl -w` (one call) / `ss -ti` (steady state).
3. CPU-bound → profile (`py-spy` / `perf` / language profiler). Do **not**
   strace.
4. Memory → `ps --sort=-rss`, `/proc/PID/smaps_rollup` (private vs shared —
   RSS over-reports shared pages), `free -h`/`vmstat si so` for system-wide
   pressure.
5. Long job → put it in `tmux` and check on it later; meanwhile these
   commands answer "is it progressing" (`pidstat -d 1 -p PID` shows its
   read/write bytes climbing; a flat I/O counter and flat CPU = it is stuck).
