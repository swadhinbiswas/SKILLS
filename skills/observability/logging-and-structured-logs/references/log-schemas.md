# Canonical structured-log field set

Use this as the org-wide contract. Deviations need a reason recorded next to
the schema.

## Required on every line

| Field | Type | Rule | Example |
|---|---|---|---|
| `ts` | string | RFC3339, UTC, explicit `Z`. Source of truth: the logging library, configured once. | `2026-03-04T14:02:11.418Z` |
| `level` | string | One of `debug info warn error critical` (lowercase). | `error` |
| `service` | string | K8s/ECS service name, not hostname. | `checkout-api` |
| `version` | string | Build id or image tag. Ties the log to the deploy. | `4f2a9c` |
| `env` | string | Enumerated: `dev staging prod`. | `prod` |
| `msg` | string | Stable event name, closed set, no interpolation. | `payment authorization failed` |

## Correlation

| Field | Rule |
|---|---|
| `trace_id` | OTel trace id (32 lowercase hex). Omit entirely on records emitted outside a trace, rather than emitting an empty one. |
| `span_id` | Current span id (16 hex). |
| `request_id` | Only if the platform has no trace context; then it must be the propagated `X-Request-Id`. Never both schemes. |
| `user_id` | Internal, non-PII id. Not an email, not a username. |

Do not emit `trace_id: ""` — an empty string is a value that looks joinable in
a query and returns nothing.

## Reserved namespaces (prefix everything)

| Prefix | Contents |
|---|---|
| `error.*` | `type`, `message`, `stack` (only for unhandled), `retryable`, `upstream` |
| `http.*` | `method`, `route` (template), `status`, `duration_ms`, `bytes` |
| `db.*` | `system` (`postgres`/`mysql`), `operation`, `duration_ms`, `rows` |
| `job.*` | `queue`, `attempt`, `duration_ms`, `outcome` |
| `outbound.*` | `peer` (logical service name), `operation`, `status` |

## Prohibited keys

`password`, `passwd`, `token`, `access_token`, `refresh_token`, `secret`,
`api_key`, `authorization`, `cookie`, `set_cookie`, `email`, `phone`,
`address`, `ssn`, `credit_card`, `card_number`, `cvv`, `iban`, `first_name`,
`last_name`, `full_name`, `date_of_birth`, `conn_str`, `dsn`, `private_key`,
`jwt`.

## Event catalogue (write one row per event, then implement it)

| `msg` | Level | Emitted when | Key fields |
|---|---|---|---|
| `order placed` | info | Order committed | `order.id`, `order.amount_minor`, `order.currency` |
| `payment authorization failed` | error | Upstream auth declined or timed out after retries | `error.*`, `payment.provider`, `attempt` |
| `payment declined` | warn | Provider returned a decline (expected, not a fault) | `payment.decline_code` |
| `rate limit hit` | warn | Rejected a request by local policy | `http.route`, `ratelimit.policy` |
| `circuit breaker open` | warn | Short-circuited a dependency | `outbound.peer`, `breaker.state` |
| `cache miss` | debug | Key absent | `cache.key_hash` (hash, not the key) |
| `slow query` | warn | Query exceeded threshold | `db.*`, `db.statement_hash` |
| `dependency timeout` | error | Timeout exhausted | `outbound.*`, `error.*` |
| `config reload failed` | critical | Startup or hot reload could not proceed | `config.key` |
| `user signed in` | info | Authentication succeeded | `user_id` |

Every event in this table should be greppable with one filter:
`msg="payment authorization failed" and env=prod`.

## Two services, one query

The point of the shared set: a request that failed at a dependency produces,
in one query across both log stores,

```
trace_id="4bf92f3577b34da6a3ce929d0e0e4736" and env=prod
```

returning, in causal order, the entry log, the outbound call, the dependency's
inbound log, its own dependency failure, and the error. No service-specific
knowledge, no text parsing.

## Minimal Python formatter (stdlib)

```python
import json, logging, sys, time
from datetime import datetime, timezone

class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, version: str) -> None:
        super().__init__()
        self.service, self.version = service, version

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc)
                     .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": self.service,
            "version": self.version,
            "env": "prod",
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None) or {}
        for key, value in extra.items():
            if key.startswith("_"):          # never shadow stdlib LogRecord attrs
                continue
            payload[key] = value
        if record.exc_info:
            payload["error"] = {"type": record.exc_info[0].__name__,
                                "message": str(record.exc_info[1]),
                                "stack": self.formatException(record.exc_info)}
        return json.dumps(payload, default=str)

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter("checkout-api", "4f2a9c"))
root = logging.getLogger()
root.handlers[:] = [handler]
root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

log = logging.getLogger("checkout")
log.info("order placed", extra={"fields": {"order": {"id": "ord_88123",
                                                    "amount_minor": 4200}}})
```

The `fields` sub-dict avoids the stdlib's reserved attribute names
(`msg`, `name`, `module`, `args`, `message`, `asctime`) — passing those via
`extra` either raises `KeyError` or silently clobbers the record, depending on
version.
