# AWS serverless service limits reference

Current hard limits, the errors they produce, and what to do instead. AWS
changes these without a major-version bump. Before you rely on a number in a
design doc or an SLA conversation, re-read it in the service's own **Quotas**
console page (or the service's documentation "Quotas" section) - that is the
authoritative, current source. This file is accurate as a starting point and
exists so you know *which* limits to check.

## Lambda

| Limit | Value | Error / symptom | Design around it |
|---|---|---|---|
| Max duration | 900 s (15 min) | `Timeout` | Anything over a few seconds belongs on SQS, not on a request path |
| Deployment package (zip) | 250 MB unzipped (direct upload); 50 MB zipped via the API | `InvalidParameterValueException` on the console/API upload | Vendor only what you import; use a container image for large deps |
| Sync invoke payload | 6 MB | `RequestEntityTooLargeException` | Presigned S3 URL for anything large |
| Async invoke payload | 1 MB | `RequestEntityTooLarge` / event rejected | Same - pass an S3 key, not the body |
| Concurrent executions | 1,000 per region (default quota, raiseable) | `TooManyRequestsException` | Reserved concurrency per function to isolate hot functions |
| Reserved concurrency | 0 disables invocation (returns 429) | `TooManyRequestsException` from the caller | Treat 0 as an intentional off switch |
| Temp storage | 512 MB (configurable to 10 GB) | `Runtime.InvalidEntrypoint` / write failure | Stream large intermediates; do not buffer in `/tmp` |
| Environment variables | ~4 KB total | Deployment error | Config from Secrets Manager / SSM, not env |
| Init (cold) duration | seconds, runtime-dependent; a large package or a heavy module-scope import dominates it | High p99 with low median | Small package, lazy imports, init outside the handler, SnapStart for JVM/.NET |

Cold-start taxonomy and mitigations are in the main skill. The practical
measurement tool is **Lambda Insights** (reads Init Duration) or **Active
Performance Insights**.

## API Gateway

| Limit | Value | Notes |
|---|---|---|
| REST API payload (request and response) | 10 MB | Exceeding it returns 413; move large bodies to S3 + presigned URL |
| HTTP API payload | larger than REST (documented separately - check current docs) | Generally more generous |
| REST API integration timeout | ~29 s (regional); can be raised for a stage | Longer than the Lambda timeout on purpose |
| HTTP API integration timeout | ~30 s | - |
| Stage throttle (account default) | 5,000-10,000 requests/s depending on stage size | `429 Too Many Requests`; raise with a request or a usage plan |
| Endpoint quota | 100 per region (REST) | Consolidate with a proxy `{proxy+}` resource |
| Deployments to one stage | one at a time (no deploy while deploying) | `TooManyRequestsException` on the stage |

HTTP API is materially cheaper per request and has lower per-request latency
than REST at high volume; it also supports OIDC/JWT authorizers natively. REST
has the broader feature set (usage plans, resource policies, request
validators). Choose per API, not per organisation.

## SQS and SNS

| Limit | Value | Error | Design around it |
|---|---|---|---|
| Message size | 256 KB | `InvalidParameterValue` / `TooLong` on SendMessage | S3 for the body, queue the key |
| Standard queue max in-flight | 120,000 (per queue, raiseable) | `RequestThrottled` / no progress | Poll or let Lambda scale; split the queue |
| Standard throughput | effectively unlimited (no ordering guarantee) | - | Default for throughput work |
| FIFO queue throughput | no quota for `SendMessage`/`ReceiveMessage`; `DeleteMessage`/`GetQueueAttributes` are limited (300 TPS default) | Throttling on the FIFO high-throughput API if you use the batch API | Use the batch API for deletes/attributes, or the higher throughput mode |
| FIFO: 300-message in-flight batches | per `ReceiveMessage`; the batch API is capped at this per call | `InvalidBatchEntryId` | - |
| FIFO deduplication window | 5 min | Duplicate delivery within the window is suppressed | Do not rely on it for correctness |
| SNS topic | 100,000 subscriptions (raiseable); 10,000 filter policies per topic | Throttling | Fan-out via S3/EventBridge, not one giant SNS topic |
| SNS/SQS message size | 256 KB | As above | - |

## DynamoDB

| Limit | Value | Error | Design around it |
|---|---|---|---|
| Item size | 400 KB | `ValidationException: Item size has exceeded the maximum allowed size` | Split into a child item, or store the blob in S3 and keep the key |
| Request item size | 1 MB (`TransactWriteItems` aggregate cap) | `ValidationException` | - |
| Attributes per item | - | The historical 100-attribute soft limit was removed; item size is the real bound | Design wide-and-short, not one mega-item |
| Read capacity per partition | 3,000 RCU/s and 1,000 WCU/s per partition | `ProvisionedThroughputExceededException` | Partition key design determines this - a hot key is unfixable after the fact |
| Attribute name / value size | name up to 64 KB, value up to 256 KB per attribute (empty strings not allowed as key attributes) | `ValidationException` | - |
| Stream / event source batch | up to 10,000 records or ~5 MB per batch for Streams; 10,000 for Kinesis; SQS up to 10 | Partial batch responses recommended | Tune batch size vs cost |

Throughput: on-demand bills per read/write request unit with a 50% eventual
consistency discount on reads, and provisioned needs a capacity plan. For a
steady, predictable load, provisioned is often cheaper; for spiky, on-demand.
Decide with a cost estimate, not a rule of thumb.

## S3 and EventBridge

| Limit | Value | Notes |
|---|---|---|
| S3 object size | up to 5 TB (single PUT up to 5 GB) | Multipart for anything over 100 MB |
| EventBridge `PutEvents` entry | 256 KB | Larger events rejected |
| EventBridge events per `PutEvents` call | 10 | - |
| EventBridge archive / replay | 12-month replay window | Useful for redriving consumers |

## Step Functions

| Limit | Value | Notes |
|---|---|---|
| Execution history events | 25,000 (Standard) | A long or chatty workflow hits `ExecutionLimitExceeded`; pass keys not payloads and keep steps wide |
| State payload | 256 KB per state input/output | Pass references |
| State machine executions in flight | account-level quota (Standard: large; Express: high throughput) | - |
| Express workflows | cannot have more than 5 in-flight `Wait` states | Choose Standard vs Express deliberately |
| Transitions | billed per state transition | Fewer, wider steps is the cost lever |

## Where to verify

- Lambda: *Lambda > Quotas* in the console; the "Limits" section of the docs.
- API Gateway: *Service Quotas > API Gateway*; the "Limits and quotas" page.
- SQS/SNS: *Service Quotas*; the SQS "Quotas and limits" page (FIFO numbers in
  particular are commonly misremembered).
- DynamoDB: *DynamoDB > Quotas*; the "Limits" page.
- Step Functions: the "Standard and Express workflows" quotas page.

## Errors you will actually see, and what they mean

| Error | Meaning | First move |
|---|---|---|
| `TooManyRequestsException` (Lambda) | Concurrency exhausted, or a throttle | Check whether it is account concurrency, reserved concurrency, or a downstream rate limit |
| `429 Too Many Requests` (API Gateway) | Stage throttle or Lambda reserved concurrency exhaustion | Distinguish gateway throttle from function throttle in the gateway's metrics |
| `504 Gateway Timeout` | API Gateway integration timeout | Lambda timeout should be lower; if not, that is the bug |
| `413` / `RequestEntityTooLarge` | Payload over the gateway or Lambda cap | Presigned S3 URL |
| `RequestEntityTooLarge` on async invoke | Over the 1 MB async cap | S3 key |
| `ValidationException: Item size has exceeded the maximum allowed size` | DynamoDB item over 400 KB | Split the item or move the blob to S3 |
| `InvalidParameterValue: The request must contain N percent` / FIFO throttles | FIFO per-action TPS | Use the batch high-throughput API, or split the queue |
| `ExecutionLimitExceeded` | Step Functions history over 25,000 events | Fewer steps; pass references; consider a queue instead of a workflow |
| `ProvisionedThroughputExceededException` | Hot partition, not total capacity | The partition key is the problem; capacity increases will not fix a single hot key |
| `ReadTimeoutError` / `EndpointConnectionError` on a warm function | Stale keep-alive connection (common behind a NAT or an idle load balancer) | Short client read timeout + retry with jitter; ensure the client is module-scope and reused |
