---
name: load-and-stress-testing
description: Design and interpret load, stress, and soak tests with k6, Locust, or JMeter - deriving realistic workloads from real traffic, reading p95/p99 and saturation instead of averages, finding the knee, testing breaking capacity safely, and diagnosing what the numbers mean. Use when a service is slow under load, before a launch or traffic spike, when choosing instance sizes, or when someone mentions latency, throughput, RPS, saturation, 429s, or "it works fine on my machine". Triggers on "load test", "k6", "locust", "jmeter", "p99 latency", "capacity planning", "stress test", "soak test", "rate limit".
compatibility: Examples use k6 and Locust; JMeter is covered conceptually. Verify CLI flags with `k6 run --help` and `locust --help` for the installed version.
metadata:
  version: "1.0"
---

# Load and Stress Testing

Load testing is measurement, not proof of performance. The deliverable is a
number with an error bar and a cause.

## SAFETY FIRST — read before generating load

**Never generate load against production without explicit, written, current
authorisation from the service owner, and never against a third party's system
at all.** "It is our own API" is not authorisation. Authorisation must name the
environment, the target hostnames, the maximum RPS, the start and end window,
and an abort contact. Keep it in the PR or ticket, not in a chat message.

Before any run:

- [ ] Written authorisation naming environment, targets, max RPS, and time window
- [ ] An abort plan: a kill switch the operator can hit without a deploy
      (stop the load generator, lower a rate limit, disable a flag)
- [ ] Non-production target with production-*shaped* data volume and topology
- [ ] Error budget / SLO checked; do not spend it on an experiment
- [ ] Dashboards and logs open **before** the first request
- [ ] A named human watching, able to stop the run
- [ ] Rate limit configured *above* the highest intended load (or the test
      measures the limiter, not the system)

Additional rules:

- **Staging is not automatically safe.** If staging shares a database, cache,
  or queue with production, staging load is production load.
- **Third-party APIs (payments, email, maps, LLM providers) must never be
  load-tested by you.** Fake them locally; their rate limits are real and being
  banned is expensive.
- **Cap total RPS at the load generator and at the target.** A mistake in the
  script (`vus` misread, a missing `duration`) can produce 100× the intended
  load in the first second. Start with a smoke run at 1% and watch.
- **Prefer a dedicated environment with a firewall to production.** Then the
  mistake is contained rather than merely detected.
- **Never run from a laptop on the office network at high RPS** — it will affect
  the shared uplink and get you noticed.

## Test types and what each answers

| Type | Question | Shape | Duration |
|---|---|---|---|
| Smoke / sanity | Does it work at all, and is the script valid? | 1–5 VUs, 1 min | 1 min |
| Load | Does it meet the SLO at expected peak? | ramp to target RPS, hold | 10–20 min |
| Stress | Where is the breaking point, and how does it fail? | step up until SLO breaks | until it breaks, then stop |
| Soak / endurance | Does anything degrade over time (leaks, cache thrash, log growth)? | steady moderate load | 4–24 h |
| Spike | Does it survive an abrupt 10× burst? | step to 10× for 60s | 5 min |
| Breakpoint | Is there a hard ceiling (pool, queue, disk)? | slow ramp, find the knee | as needed |

Run load → stress → soak in that order. A soak test on a system that fails at
2× load teaches you nothing.

## Step 1 — build the workload from real traffic

Do not invent a profile. Mine it:

- **Access logs / APM** for the top endpoints by request count, and the real
  ratio between them (a test that hits only `/search` proves `/checkout` works
  at 0 rps).
- **Route frequency × real payload shapes** (p50/p95 body sizes — the mean hides
  the fat request that is your bottleneck).
- **Real session shape:** think time between actions, arrival pattern
  (Poisson/uniform, not "everyone at t=0"), and the funnel (many logins, few
  checkouts).
- **Data distribution:** 80/20 skew. If 1% of users generate 40% of traffic,
  a uniform test will never touch the code path that is on fire.

Model **three** scenarios, not one:

1. **Typical day** (average RPS).
2. **Peak hour** (p95 of real RPS × safety factor).
3. **Failure amplifier** — one dependency slow or down. This is the test that
  finds missing timeouts and unbounded queues, and it is the one most often
   skipped.

## Step 2 — pick a tool

| Tool | Best for | Not for |
|---|---|---|
| **k6** (Go, single binary, JS scripts) | HTTP/GraphQL, CI-runnable, threshold-as-code | Heavy browser flows, thousands of distinct VUs |
| **Locust** (Python, web UI, code-defined users) | Complex logic, custom auth flows, datasets from files, live tweaking | Very high VU counts on one box |
| **JMeter** (Java, GUI + XML) | Large existing suites, non-HTTP protocols (JDBC, JMS, FTP) | New work — heavy, hard to review in git |
| **vegeta / hey / wrk** | One-shot sanity against a single endpoint | Multi-scenario workloads |

House default: **k6 for HTTP**, because a script is a text file in git, the
thresholds are declarative, and CI can run it without a GUI. Use Locust when the
scenario needs real application logic (log in, create data, assert mid-flow).

## Step 3 — write the k6 script

```js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const base = __ENV.BASE_URL || 'http://localhost:8080';
const checkout = new Trend('checkout_duration', true);   // true = time metric
const failures = new Rate('business_failures');

export const options = {
  scenarios: {
    peak: {
      executor: 'ramping-arrival-rate',   // arrival rate = requests/sec, not VUs
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 200,
      stages: [
        { target: 100, duration: '2m' },  // ramp to peak RPS
        { target: 100, duration: '10m' }, // hold: this is the measurement window
        { target: 0,   duration: '1m' },
      ],
    },
  },
  thresholds: {
    'http_req_duration{expected_response:true}': ['p(95)<300', 'p(99)<800'],
    'http_req_failed': ['rate<0.01'],
    'checkout_duration': ['p(95)<500'],
    'business_failures': ['rate<0.005'],
  },
};

export default function () {
  // session shape: a few reads with realistic think time between them
  const headers = { 'Content-Type': 'application/json' };
  const login = http.post(`${base}/login`, JSON.stringify({ user: 'load@x.test', pass: 'x' }), { headers });
  check(login, { 'login ok': (r) => r.status === 200 });
  const token = login.json('token');

  sleep(randomIntBetween(1, 3));        // think time: 1-3s between actions

  const order = http.get(`${base}/orders/${randomIntBetween(1, 100000)}`, {
    headers: { Authorization: `Bearer ${token}` },
    tags: { name: 'GET /orders/:id' },
  });
  check(order, { 'order ok': (r) => r.status === 200 });

  const res = http.post(`${base}/checkout`, JSON.stringify({ cartId: 'c-1' }), {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    tags: { name: 'POST /checkout' },
  });
  const ok = check(res, { 'checkout 2xx': (r) => r.status === 200 || r.status === 201 });
  failures.add(!ok);
  checkout.add(res.timings.duration);
  if (!ok) console.log(`checkout failed: ${res.status} ${res.body}`);
}
```

Run it:

```bash
k6 run --out json=results.json script.js
k6 run --summary-mode=full --quiet script.js        # for CI logs
k6 run -e BASE_URL=https://staging.example.com -e RATE=50 script.js
```

**Prefer `ramping-arrival-rate` or `constant-arrival-rate` over VUs.** With VUs,
a slow endpoint silently reduces the request rate, so the test gets *less*
load the more the system struggles — the opposite of a stress test. Arrival-rate
executors hold the offered load constant and let latency and errors tell the
story.

**VUs are not concurrency if the script sleeps.** 100 VUs with 2s of think time
is ~50 rps, and it is a closed loop: it measures "how fast can the system go
when everything pushes as hard as they can", not "what happens at N rps".

**Tag every request** (`tags: { name }`) so per-endpoint percentiles are
readable. Without tags, the histogram mixes `/health` with `/checkout` and the
p99 is meaningless.

## Locust, when the scenario needs real logic

```python
from locust import HttpUser, task, between, LoadTestShape

class Shopper(HttpUser):
    wait_time = between(1, 3)              # think time

    def on_start(self):
        r = self.client.post("/login", json={"user": "load@x.test", "pass": "x"})
        self.token = r.json()["token"]

    @task(5)                               # weighted: 5 reads per 1 write
    def browse(self):
        self.client.get(f"/orders/{self._id()}", headers=self._h(),
                        name="GET /orders/:id")

    @task(1)
    def checkout(self):
        with self.client.post("/checkout", json={"cartId": "c-1"},
                              headers=self._h(), name="POST /checkout",
                              catch_response=True) as r:
            if r.status_code == 429:
                r.failure("rate limited")   # a 429 is a load-test artefact
            elif r.status_code >= 500:
                r.failure(f"server error {r.status_code}")

    def _h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _id(self):
        from random import randint
        return randint(1, 100000)

class PeakHour(LoadTestShape):
    """Constant arrival rate, so a slow system does not reduce the load."""
    def tick(self):
        return (200, 1)                   # 200 users, spawn 1/s, hold
```

```bash
locust -f load_test.py --host http://localhost:8080 --headless \
      --users 200 --spawn-rate 20 --run-time 15m --csv results
```

Mark a non-2xx response as a *failure* explicitly where the default is wrong
(`catch_response=True`); by default Locust treats 4xx as success, which hides
exactly the errors you are hunting.

## Step 4 — read the results correctly

**Never report the mean.** Averages are dominated by the fast path; a service
with a 5 ms mean and a 9 s p99 is broken and the average will not tell you.

Read, in this order:

1. **Error rate by status code, over time.** A rising 5xx curve as RPS climbs is
   the breaking point. A flat 200-then-429 wall is the rate limiter, not the
   service — the limiter is doing its job and you learned nothing.
2. **p95 and p99 latency per endpoint (tagged), over time.** A p50 that is flat
   while p99 doubles is a tail problem: GC, lock contention, cold cache, a slow
   downstream, connection pool waits.
3. **Saturation signals** — the knee. Watch: CPU, memory, run queue, request
   queue depth, active DB connections vs pool size, thread pool queue length,
   container throttling, disk I/O wait, GC pause time. **The first metric to
   saturate is usually not the one you were watching.**
4. **Throughput curve.** If RPS stops rising while VUs keep coming, you have
   found saturation. Throughput should be roughly flat once saturated, and
   latency should rise steeply — that is the knee.
5. **Queueing.** Little's law: `concurrency = throughput × latency`. If
   concurrency is fixed and latency rises, throughput fell — you are past the
   knee. This is the most reliable "are we saturated" statement you can make.

Diagnostics by shape:

| Shape | Likely cause |
|---|---|
| Latency flat, errors appear suddenly | Hard limit hit: pool exhausted, file descriptors, disk full, licence, connection refused to a dependency |
| Latency rises smoothly, errors stay flat | Backpressure working (queueing) — approaching the knee |
| p50 fine, p99 explodes, CPU moderate | GC pauses, lock contention, cache misses, one slow shard, log fsync |
| All requests slow, CPU idle | Blocking I/O, thread pool starvation, a slow synchronous dependency, DNS |
| Latency rises while throughput *falls* | Overload collapse: retries amplifying load, thundering herd, connection churn |
| Everything fine except one endpoint | That endpoint's query or dependency; test it in isolation with the same data volume |

## Step 5 — stress, then soak, then break deliberately

- **Stress:** step RPS in 25% increments and hold each step 2–3 minutes. Stop
  at the first SLO breach and record that RPS as the knee. *Always* ramp down
  gently and confirm the service recovers — a system that does not recover after
  the load stops has a worse problem than a slow one.
- **Break deliberately** (only in an isolated environment, only with the
  authorisation above): kill a dependency, drop the DB connection pool, fill
  disk, add 500 ms latency to a downstream. The goal is to watch the failure
  propagate predictably: timeouts, circuit breaker, bounded queue, no
  unbounded retry storm. **An unbounded retry loop is the most common finding
  in this whole skill** — every layer retries, so 3 layers of retry at 3× turns
  a 10% error rate into a self-inflicted outage. Cap retries, add jitter, and
  only retry idempotent operations.
- **Soak:** steady 60–70% of the knee for 4–24 h. You are hunting: memory
  growth (leak, unbounded cache), connection count creep, log/disk growth,
  index/table bloat, a job that never completes, a circuit breaker stuck open.
  Compare a heap/graph snapshot or `EXPLAIN`-level metrics at hour 1 and hour
  8 — a linear trend is the finding.

## Interpreting and reporting

Every report states: environment and data volume, workload model and its source,
RPS offered vs achieved, p50/p95/p99 **per endpoint**, error rate by code, the
knee, the saturation metric, and a confidence note (run twice; a single k6 run
on a shared box has ±10% noise). No report says "performance improved".

Numbers must be comparable: same dataset size, same topology, same region. A
"40% faster" claim across a dataset change is a false claim.

## Gotchas

- **The load generator is also a client.** A single k6 box saturates at a few
  thousand RPS and then measures *itself*; the run will show a latency knee that
  is the generator's CPU. Watch the generator's CPU, distribute it, and treat
  its saturation as an invalid run. Distributed k6 (`k6 run --execution-segment`)
  or Locust workers exist for this.
- **Keep-alive and connection reuse change the result.** A test that opens a new
  connection per request measures the accept path, not the service. Use keep-alive
  and separately test cold-start (new connection, cold cache, JIT warm-up) —
  a JVM or a .NET service can look fine warm and fall over on a cold pod.
- **Warm-up matters.** Run a short warm-up phase before measuring, and discard
  it. Caches, connection pools, and JIT need to be warm, or you are measuring
  startup.
- **Test data volume must match production.** 100 rows and 10 million rows hit
  completely different plans, index decisions, and buffer behaviour. A pass on a
  tiny dataset is not a pass.
- **`429` and `503` from your own rate limiter are the limiter working**, not a
  service failure. Either raise the limit above the test ceiling before the run,
  or count them separately.
- **A cache warmed by the test is not a cache warmed by users.** Sequential
  access patterns in a script produce unrealistically high hit rates. Mix in
  random keys, or clear the cache before the run.
- **DNS and TLS handshakes are part of your load.** Pre-resolving DNS, or
  measuring only the handler, hides real cost. And timeouts: a script with no
  timeout will hang on the first slow response and stall the ramp.
- **Closed-loop hides queueing; open-loop exposes it.** With VUs, the system
  never sees more than N concurrent requests. That is why a service with 50
  connections can look fine at 100 VUs and fall over for 100 real users.
- **Never test a deployment you are also deploying.** Load-test a specific,
  immutable artifact (image digest, git SHA), and record which one.
- **A single run is data, not a decision.** Variance between identical runs is
  often ±10–20% on a shared runner; without a baseline you cannot claim an
  improvement.

## Files

- `references/k6-locust-recipes.md` — copy-paste scenarios: arrival-rate
  capacity ramp, spike, soak, dependency-failure injection, and Locust
  datasets/custom shapes. Read it when you need a specific test shape and do
  not want to derive it.
