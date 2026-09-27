---
name: aws-serverless-architecture
description: Design AWS serverless systems that survive contact with real limits - Lambda cold starts, API Gateway payload and timeout caps, sync vs async invocation and error handling, SQS/SNS/Step Functions event flow, DynamoDB connection management, and the cost model. Use when a user pastes "Too many requests" or a Lambda timeout, when choosing between API Gateway/Lambda/Step Functions, when a fan-in queue design is being reviewed, or when Lambda bills are surprising.
compatibility: AWS serverless (Lambda, API Gateway, DynamoDB, S3, SQS, SNS, Step Functions, EventBridge). Service quotas change often - every hard number below is a current value you should re-verify in the service's "Quotas" page before relying on it.
metadata:
  version: "1.0"
---

# AWS Serverless Architecture

Serverless is a set of **managed services with hard limits and a different
failure model** than a server. The limits are not edge cases; they are the
architecture. Design against them.

Exact quota values live in
`references/service-limits.md` - read it before quoting a number to anyone,
because AWS raises these without notice. The design guidance below is stable
even when a number changes.

## The default topology

```
client
  -> API Gateway (REST/HTTP API)         auth, throttling, request/response
      -> Lambda (thin handler: parse, authz, enqueue, respond)   compute
          -> DynamoDB (single table, PK/SK)                       state
          -> SQS (one queue per work type, DLQ each)              buffer + retry
              -> Lambda (worker)  /  ECS Fargate for long work
                  -> S3 (lifecycle to cheap tiers)                blobs
  -> S3 event -> SQS -> Lambda          async, no API Gateway in the path
  -> Step Functions for multi-step, only when fan-in/wait is genuinely needed
```

Design rules that follow from that shape:

- **API Gateway handlers are thin and synchronous; they do not do work.** They
  validate, authorise, enqueue, and return. Anything that can exceed a few
  seconds goes through SQS. This is what keeps p99 latency and the timeout
  class of bugs off the request path.
- **One SQS queue per work type**, not one per tenant or per item. Per-tenant
  queues explode your account's queue limit and make per-queue scaling
  impossible. Use a queue per *class* of work and tag messages for routing.
- **Every queue has a DLQ**, and the DLQ alarm is on from day one. A silent DLQ
  is a data loss with a delay.
- **Fan-in through SQS, not through Step Functions, for the common case.** A
  Step Function that waits for N parallel tasks costs money per state
  transition and per wait; a DynamoDB counter plus SQS costs a fraction. Reach
  for Step Functions when the workflow is genuinely branching, long-lived
  (hours/days), or needs a human-in-the-loop - not just to sequence two steps.
- **Async to the client where possible.** If the client does not need the
  result, return 202 with a job id and do the work off the request path. That
  removes the API Gateway timeout, the Lambda timeout, and most of the retry
  storm, in one decision.

## Cold starts: know which one you have

"Cold start" is three different problems with three different fixes.

| Kind | What is slow | Fix |
|---|---|---|
| **Deployment** (minutes-scale, rare) | New version, new execution environment, no reuse | Provisioned concurrency, or accept it; affects only newly published versions |
| **Init** (hundreds of ms - seconds) | Runtime unpack, import, global init | Keep the zip small; init globals **outside** the handler; lazy-load what you do not need on the hot path |
| **Restore** (seconds, the real one) | SnapStart restoring a JVM/.NET snapshot, or a large `init` | SnapStart, or make init trivial |

Practical defaults:

- **Package discipline beats every other lever.** Deploy only what the handler
  needs. A 250 MB deployment package adds to init time on every cold start; a
  5 MB one does not. Use `provided.al2023` (or the Node/Python managed runtime)
  and vendor only the modules you import.
- **Move initialisation out of the handler.** Anything at module scope runs on
  every cold start and is free afterwards:
  ```python
  # GOOD: client built once per environment, reused
  import boto3
  DDB = boto3.resource("dynamodb", endpoint_url=os.environ.get("DDB_ENDPOINT"))
  TABLE = DDB.Table(os.environ["TABLE"])

  def handler(event, context):
      ...  # uses TABLE
  ```
  A client built *inside* the handler is rebuilt on every warm invocation too -
  a correctness-neutral but real latency and connection-churn bug.
- **Never assume a warm container.** Globals are initialised per execution
  environment, and environments are recycled; cache anything expensive in a
  global, and treat that cache as "may be empty".
- **Measured latency, not vibes.** Enable **Lambda Insights** or Active
  Performance Insights and read *Init Duration*, *Duration*, and *Memory
  allocated* over a real traffic window before optimising. A slow handler is
  usually I/O or a too-small memory size, not a cold start.
- **Memory is the biggest performance lever**, because CPU is allocated in
  proportion to memory. A memory-starved function is CPU-starved. Use the
  AWS Lambda Power Tuning state machine to find the knee of the cost/latency
  curve, then set memory there.
- **Provisioned concurrency** is the only real fix for a strict-latency
  endpoint, and it costs money whether or not it is used. Use it on the API
  path for a genuinely latency-critical service; do not use it as a default.
  Note that provisioned concurrency is billed per provisioned instance, and
  **it does not apply to a function that is not SnapStart/managed-runtime
  eligible** - verify eligibility for your runtime before relying on it.
- **Never depend on ordering across invocations.** Concurrent invocations of
  the same function run in parallel. Anything that assumes "the previous
  invocation finished first" is a bug waiting for a retry.

## Timeouts, invocation modes, and retries

**Timeouts are a three-way negotiation.** API Gateway has its own integration
timeout, Lambda has a maximum function timeout, and the client has a patience
limit. Set them in that order, and make the *inner* limit the binding one:

- Set the **Lambda timeout below** the API Gateway integration timeout, so
  Lambda returns a structured error before the gateway cuts the connection.
  Otherwise the client gets a 504 with no error body and no trace.
- Set the **API Gateway timeout below** the client/load-balancer timeout, so
  the gateway returns a proper JSON error.
- A Lambda function with a 15-minute timeout is not a request/response
  function; it is a batch function. Put it behind SQS.

**Invocation modes:**

| Mode | Who retries | Client sees | Use for |
|---|---|---|---|
| **Sync** (API Gateway) | The caller/API Gateway | The result | Request/response where the client needs the answer |
| **Async event** (S3, SNS, EventBridge) | The service, with retries and a DLQ | Nothing | Fire-and-forget; the service retries on function error |
| **Async from SQS** | SQS, on function error and on visibility-timeout expiry | Nothing | Buffering, retries, smoothing bursts, decoupling |
| **Poll-based** (you ask Lambda) | Nobody | Timeout | Batch; use Batch instead |

- **Do not call SQS `ReceiveMessage` from a Lambda.** That is the poller
  pattern, which is what the SQS/Lambda *event source mapping* replaced. With
  an event source mapping, Lambda polls, scales, and reports partial batch
  failures; you do not write a loop.
- **Partial batch responses** (on by default for SQS/Kinesis/DynamoDB Streams
  event source mappings): return `{batchItemFailures: [{itemIdentifier: "..."}]}`
  and only those messages are retried. Return `null`/empty and the whole batch
  is acknowledged. This is how you get exactly-once-*effect* semantics out of
  at-least-once delivery: **the message may be delivered twice, so the handler
  must be idempotent.** Say so in a comment, because it is the single most
  important correctness property of this architecture.
- **Event source mapping scaling** is not instant. `MaximumBatchingWindowInSeconds`
  + partial batch responses is the recommended default (start at a few seconds
  for SQS) - it reduces invocations and cost without meaningfully hurting
  latency. Setting it to 0 makes every message a potential cold-ish invocation.
- **An async event source that invokes a function that errors retries and then
  sends to the function's DLQ, not the queue's.** The queue's DLQ is for
  messages that exceeded `maxReceiveCount`. They are different failures and both
  need alarms.

## Payload and size limits (the ones that actually bite)

- **API Gateway** caps the request and response body size; REST and HTTP APIs
  differ. A large upload must not go through the gateway - put a **presigned
  S3 URL** in the response and have the client upload directly. This one change
  removes the most common serverless gateway failure.
- **Lambda** synchronous invoke payload is 6 MB; **asynchronous invoke is
  1 MB** (S3 still works because the service passes an S3 reference, not the
  body). The async number is the one that surprises people: a 2 MB event that
  worked synchronously fails on the async path.
- **SQS** message cap is 256 KB. Exceed it and you get a real error at
  `SendMessage`. For bigger payloads, put the blob in S3 and send the key.
- **SNS** message cap is 256 KB as well.
- **Step Functions** state payload and history size are capped; a state that
  carries a large document hits a limit and fails the execution. Pass keys, not
  payloads.
- **DynamoDB** item cap is 400 KB. A single item cannot be bigger, period - the
  fix is a separate item or S3, not a bigger "item".
- **EventBridge** event size is capped (256 KB for `PutEvents` and for what it
  will route); larger events are dropped or rejected with a size error.

Exact current values, and the ones to re-verify, are in
`references/service-limits.md`. The design consequence is the same for all of
them: **pass references, not payloads.** Large data goes to S3; events carry
keys.

## DynamoDB from Lambda: the connection-management trap

The single most common performance bug in serverless: **creating a client per
invocation**, or **holding a client open and letting it go stale**.

- Create the resource **once per execution environment** (module scope). The
  client reuses the underlying TCP/HTTP connections across invocations.
- Boto3's connection pool has a **lifetime** and does not reliably detect a
  connection killed by an idle NAT gateway or a recycled proxy. The classic
  symptom: a function that is fine under load and starts throwing
  `EndpointConnectionError` / `ReadTimeoutError` after the connection has been
  idle, or "sporadic" errors minutes after a deploy. Mitigations: a short
  HTTP client `read_timeout`/`connect_timeout` (so a dead connection fails
  fast and is retried inside your code), and reusing the client rather than
  rebuilding it. A keep-alive age far shorter than the NAT idle timeout is the
  cleaner fix; if you cannot control the NAT, set client timeouts and add a
  bounded retry with jitter.
- **Do not enable DynamoDB connection management with a reserved concurrency
  of 1** without understanding why; and note that a very low reserved
  concurrency (to throttle a hot function) causes **connection starvation**
  (many concurrent invocations multiplexed onto few TCP connections), which
  shows up as timeouts. If you throttle Lambda concurrency, expect to relieve
  downstream connection pressure, and verify you are not making it worse.
- **Design the table for the access pattern**: single-table with
  `PK`/`SK` and an explicit access pattern per query; a GSI only where you have
  a real second pattern; and a key attribute that never needs a "scan
  everything" query. Know the RCU/WCU cost of each pattern before you commit to
  it (a `Scan` on a large table is a production incident).
- **Use `DynamoDB Streams` + an event source mapping** for change propagation
  (search indexing, cache invalidation) with partial batch responses, not a
  polling Lambda.
- **Consistency**: `GetItem` is eventually consistent by default; a
  read-after-write on the same item needs `ConsistentRead: true` (double the
  RCU). Choose per read, not globally, and document why.

## S3: events, static hosting, and cost

- **S3 event notifications go to SQS, SNS, Lambda, or EventBridge** - and the
  delivery is **at least once** and unordered. Your consumer must be idempotent
  and must handle duplicates and out-of-order events. Configure
  `FilterRule`/`Filter` prefixes and suffixes to avoid a Lambda invocation per
  object when the bucket holds other content - this is the cheapest serverless
  cost optimisation there is.
- **Static hosting from S3**: the site is served by CloudFront (S3 website
  endpoints do not support HTTPS; use CloudFront + a certificate, or S3 REST
  endpoints with CloudFront in front). Private buckets + CloudFront Origin
  Access Control, so the bucket itself is not public.
- **Lifecycle to cheap tiers** is a cost lever, not an afterthought: after N
  days move objects to Infrequent Access / Glacier Instant Retrieval / Glacier
  Flexible Retrieval, and to Deep Archive after longer. Deep Archive objects
  cannot be read immediately; retrieving them takes hours. Choose the transition
  windows from the actual access pattern. (See `cloud-cost-optimization`.)
- **Presigned URLs** for upload/download, short expiry, scoped to a key
  prefix and a content type - this is the correct answer to "the payload is too
  big for the gateway".

## Cost model: where serverless money actually goes

Serverless is not automatically cheaper. The cost centres, roughly in the order
they surprise people:

- **Lambda invocations** - priced per million requests *and* per GB-second of
  duration. Many tiny invocations are request-dominated; a memory-hungry
  long-running function is duration-dominated. Batching (DynamoDB/SQS batch
  size, `MaximumBatchingWindowInSeconds`) attacks requests; right-sizing memory
  and duration attacks GB-seconds.
- **API Gateway** - per million requests, plus a **stage/caching** tier, plus
  data transfer out. An HTTP API is materially cheaper per request than a REST
  API at high volume; check the current pricing pages for the break-even.
- **NAT Gateway** - billed **per hour, per gateway, per GB processed**, and it
  is the classic "my serverless bill went up 10x" item because every Lambda in
  a private subnet needs one. This is the bridge to
  `cloud-networking-and-vpc`: design egress so you do not need a NAT for
  AWS-internal traffic.
- **DynamoDB** - on-demand vs provisioned; on-demand can cost more than
  provisioned for a steady load; RCU/WCU and storage; and **data transfer out**
  to the internet is not free.
- **Logs** - **CloudWatch Logs ingestion and retention** is frequently larger
  than the compute. Set a retention period (never `NEVER`), drop `DEBUG` to
  nowhere in production, and do not log full request/response bodies. A
  `console.log` of a 100 KB payload is a real bill line.
- **Data transfer out** to the internet, and **cross-AZ** traffic inside a
  region. Architecting for AZ-locality (RDS Multi-AZ failover is cross-AZ by
  design) is a cost and latency decision.

Cost-control defaults: a log retention of 14-30 days on everything; alarms on
monthly spend per service; one budget per environment with an email to the
owner; and a tag on every resource including the function name and environment,
so a bill can be attributed. Detail in `cloud-cost-optimization`.

## Security defaults for serverless

- **The function's execution role is the security boundary.** It is what
  accesses S3/DynamoDB/SQS. Grant it exactly what the code uses, on specific
  ARNs. Do not pass the caller's credentials into the function "just in case".
- **API Gateway auth**: use a JWT authorizer (Cognito) or an IAM-authenticated
  REST API; do not put long-lived tokens in query strings (they land in access
  logs). Authorise *in the function* on the caller's identity claims - API
  Gateway can validate a token, it cannot know whether this caller may read
  *this* record.
- **Never put secrets in environment variables of a function that a wider
  audience can read the configuration of**; prefer Secrets Manager and fetch at
  cold start. `TF_VAR_`-style leakage into Terraform state is the usual path in
  - see `terraform-state-management`.
- **VPC-attached Lambda** gets ENIs in your subnets, which brings NAT and
  cold-start cost. Only attach when the function genuinely needs to reach
  something inside the VPC; otherwise it will not have public network access to
  reach AWS APIs easily and you will have built the NAT bill yourself.
- **Resource policies on S3/SQS are the second gate** (see
  `aws-iam-and-security`): an SQS queue policy can restrict who may
  `SendMessage`, which limits a confused-deputy abuse of your endpoint.

## Gotchas

- **Async Lambda payloads cap at 1 MB, sync at 6 MB.** A handler that passes in
  both fails on only one of them.
- **`Too many requests` / `429` from a Lambda-originated API** is usually an
  API Gateway throttle or a **reserved concurrency exhaustion** on the function
  (the function returns 429 to the gateway when its own concurrency is
  exhausted). Read which one before you raise limits.
- **A gateway 504 with an empty body** is the gateway timing out, not the
  function erroring. Raise nothing until you find which timer fired.
- **A function that times out mid-DynamoDB-write has no idea whether the write
  happened.** The retry re-applies it. This is why handlers must be idempotent
  (conditional writes, unique keys, `attribute_not_exists` guards), not
  because SQS might duplicate but because a timeout is indistinguishable from
  a failure.
- **DynamoDB condition expressions are not a transaction.** `PutItem` with a
  `ConditionExpression` is an atomic check-and-set; multi-item invariants need
  `TransactWriteItems`. Reading-then-writing without a condition is a race.
- **SQS `maxReceiveCount` + long visibility timeout** means a slow handler
  (near the visibility timeout) can have its message redelivered *while the
  first attempt is still running*. Set the visibility timeout to comfortably
  exceed the function's worst-case p99 duration, plus retries. This is a very
  common cause of duplicate processing.
- **Fan-out with SNS to many SQS queues** is fine, but a subscriber queue that
  is deleted or whose policy forbids SNS will make publishes fail (or silently
  drop) - SNS retries, and a misconfigured subscriber is hard to see. Monitor
  `PublishFailure`/`NumberOfNotificationsFailed`.
- **Step Functions costs scale with *state transitions*, not wall time.** A
  workflow with a 1-minute `Wait` still costs a transition, but an HTTP task
  that retries 5 times costs 5 transitions. Design for fewer, wider steps.
- **Lambda `@connections` / keep-alive**: reuse HTTP clients in your code (the
  Node/Python SDKs do this); do not rely on the runtime to pool sockets for
  arbitrary libraries. Disable keep-alives is the wrong default - a closed
  connection per call is a handshake per call.
- **A function's timeout is not a retry budget.** Lambda does not retry a
  function that times out; the caller (API Gateway) does, and the client may
  too. Budget for amplification.
- **Reserved concurrency at 0 is a hard "off switch"** and returns a
  throttling error, not a queued request. Concurrency 1 serialises everything
  and can cause downstream connection starvation.
- **API Gateway REST API stage variables and request templates silently
  stringify objects**; large nested bodies get truncated by the payload cap, not
  by a validation error. Validate size explicitly at the edge.
- **CloudWatch metrics for custom latency** are only emitted if you emit them.
  Enable the `serverless-plugin` pattern (or Powertools) so you have
  `p99`, errors, and throttles per function - you cannot debug a serverless
  service from Lambda's default `Duration`/`Errors` alone.

## Safety notes

- Every SQS queue: a DLQ, a redrive policy, and an alarm on DLQ depth > 0.
  A DLQ with no alarm is a silent data-loss incident.
- Make handlers idempotent and say so in the code, or hand-rolled retries
  will duplicate side effects (charges, emails, rows).
- Never put a credential in a Lambda environment variable that is not needed,
  and never in an API response, log line, or error message.
- Budget alarms and per-service spend tags before the first production deploy,
  not after the first surprise.
- For anything that writes to a third party (payment, email, SMS), make the
  retry path explicitly safe (idempotency keys), because async delivery will
  duplicate.
