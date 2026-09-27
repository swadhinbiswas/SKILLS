# k6 and Locust recipes

## k6: capacity ramp that finds the knee

Ramp in steps, hold each step long enough to reach steady state, and stop at the
first threshold breach. Thresholds fail the run — that is the abort switch.

```js
// k6 run --out json=results.json capacity.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const base = __ENV.BASE_URL;
const SLO_P95 = 300, SLO_P99 = 800;
const step = new Trend('step_latency', true);
const err = new Rate('errors');

export const options = {
  scenarios: {
    // One step per STAGE_RPS value; `startRate` holds the offered rate constant.
    steps: STAGE_RPS.map((rps) => ({
      executor: 'ramping-arrival-rate',
      startRate: rps,
      timeUnit: '1s',
      preAllocatedVUs: Math.ceil(rps * 0.5) || 10,
      maxVUs: rps * 3,
      stages: [{ target: rps, duration: '2m' }, { target: rps, duration: '3m' }],
      gracefulStop: '30s',
    })).reduce((all, s, i) => (i ? { ...all, [`step${i}`]: s } : { step0: s }), {}),
  },
  thresholds: {
    'http_req_duration': [`p(95)<${SLO_P95}`, `p(99)<${SLO_P99}`],
    errors: ['rate<0.01'],
  },
  // If p95 exceeds the SLO at any point, abort: no point loading a broken system.
  thresholdsAbortEarly: true,
};

// STAGE_RPS comes from -e STAGE_RPS=50,100,200,400
export default function () {
  const r = http.get(`${base}/api/search?q=widget`, { tags: { name: 'GET /search' } });
  const ok = check(r, { '2xx': (x) => x.status === 200 || x.status === 204 });
  err.add(!ok);
  step.add(r.timings.duration);
  sleep(Math.random() * 2);
}
```

Pass stages as a single comma-separated env var rather than generating objects
inline; k6's config is evaluated before the file runs, so anything computed at
module scope must be pure and derived only from `__ENV`. Check
`k6 run --help` for the exact CLI for the version you have — the flag for
setting env is `-e KEY=VALUE` and has been stable.

To find the knee automatically, run each stage as a separate k6 process from a
loop and keep the highest RPS whose p95 stayed under the SLO. A single
multi-stage run's percentiles are cumulative and will not tell you which step
broke.

## k6: spike

```js
export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-arrival-rate',
      startRate: 50, timeUnit: '1s',
      preAllocatedVUs: 200, maxVUs: 1000,
      stages: [
        { target: 50,   duration: '3m' },   // warm, steady
        { target: 500,  duration: '5s' },   // 10x burst, fast
        { target: 500,  duration: '1m' },   // hold the burst
        { target: 50,   duration: '30s' },  // recovery: does it come back?
        { target: 0,    duration: '10s' },
      ],
    },
  },
};
```

Watch during the burst: does the system shed load (fast failures, 503) or accept
it all and collapse into timeouts? Shed is healthy. Also confirm the recovery
step: a system that does not return to baseline latency within a minute of the
spike ending has queue backlog that will bite the next spike.

## k6: soak (4+ hours)

```js
export const options = {
  scenarios: {
    soak: {
      executor: 'constant-arrival-rate',
      rate: 40, timeUnit: '1s', duration: '8h',
      preAllocatedVUs: 60, maxVUs: 120,
    },
  },
  thresholds: {
    // Trend assertions catch slow drift that an aggregate would hide.
    'http_req_duration': ['p(95)<300', 'p(99)<600'],
    'http_req_duration{expected_response:true}': ['max<1500'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};
```

Run with `--summary-export` and diff two summaries (hour 1 vs hour 8). Look for
trends in: heap/RSS (app-side, from your own `/metrics` if exposed), DB
connections, table size and index bloat, log file size, file descriptors,
goroutine/thread counts, and the rate of a metric that only ever grows (a cache
that never evicts, a queue that never drains).

## k6: dependency failure injection

Requires a fault proxy. Options: Toxiproxy (has k6 examples), a reverse proxy
you control, or a fake upstream you toggle via an env var/endpoint. The point
is to observe *this* system's behaviour, not the proxy's.

```js
// Assumes a proxy at PROXY that can be told to fail a named upstream.
const proxy = __ENV.FAULT_PROXY;

function maybeBreak(name, mode) {
  if (!proxy) return;
  http.post(`${proxy}/proxies/${name}`, null, {
    body: mode, headers: { 'Content-Type': 'application/json' },
  });
}

export function setup() {
  maybeBreak('inventory-service', 'timeout');     // hold open, never respond
  return { t0: Date.now() };
}

export default function () {
  const r = http.get(`${base}/api/checkout`, { timeout: '5s', tags: { name: 'GET /checkout' } });
  check(r, {
    'did not hang': (x) => x.timings.duration < 5500,
    'returned a controlled error': (x) => x.status === 503 || x.status === 200,
  });
  // assert the circuit breaker opened, and that requests are not retried
  // into the void: the VU count must be stable, not climbing.
}

export function teardown() {
  maybeBreak('inventory-service', 'none');
}
```

Assert three things, in order: (1) timeouts are bounded and configured, (2) the
failure is a controlled 5xx with a stable code, not a hang or a stack trace,
(3) **the retry count is bounded** — instrument it and assert on it, because an
unbounded retry chain is the finding this test exists to produce.

## Locust: dataset-driven and custom shapes

```python
import csv, random
from locust import HttpUser, task, between, LoadTestShape, events

# Load the dataset once per worker process.
IDS = []
with open("data/order_ids.csv") as fh:
    IDS = [row["id"] for row in csv.DictReader(fh)]

@events.test_start.add_listener
def on_start(environment, **_):
    environment.stats.csv = "results.csv"

class Shopper(HttpUser):
    wait_time = between(1, 3)
    weight = 10                     # 10 shoppers per 1 admin in the mix

    def on_start(self):
        self.client.headers.update({"Authorization": f"Bearer {self.token}"})
        self.user_id = random.choice(IDS)

    @task(20)
    def browse(self):
        self.client.get(f"/orders/{self.user_id}", name="GET /orders/:id")

    @task(1)
    def checkout(self):
        with self.client.post("/checkout", json={"cartId": "c-1"},
                              name="POST /checkout", catch_response=True) as r:
            if r.status_code == 429:
                r.failure("RATE LIMITED (lower the test rate or raise the limit)")
            elif r.status_code >= 500:
                r.failure(f"5xx {r.status_code}")
            elif r.status_code not in (200, 201):
                r.failure(f"unexpected {r.status_code}")

class SoakShape(LoadTestShape):
    """4h plateau after a 5m ramp. `tick` returns (user_count, spawn_rate)."""
    def tick(self):
        t = self.get_run_time()
        if t < 300:
            return (20, 5)
        if t < 4 * 3600:
            return (100, 10)
        return None
```

```bash
locust -f load_test.py --headless --host "$BASE_URL" \
       --run-time 4h --csv results --html report.html
```

## Making the report readable

- **Percentiles per endpoint, per run, plus the RPS offered vs achieved.** If
  achieved RPS is far below offered, the generator or the system was the limit —
  say which.
- **Plot latency over time**, not just the summary. A flat average with a rising
  p99 is invisible in a summary table and obvious in a graph.
- **Put saturation metrics next to the latency graph** on the same time axis.
  Correlation is the finding.
- **Record the artifact under test** (image digest / commit SHA) and the
  environment. A load result for "latest" is not a result.
