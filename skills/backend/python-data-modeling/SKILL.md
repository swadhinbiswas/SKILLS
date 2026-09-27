---
name: python-data-modeling
description: Model and validate data in Python 3.11+ - pydantic v2 models (validators, strict vs coercion, settings), dataclasses vs attrs vs NamedTuple vs __slots__ classes, and when NOT to put pydantic on an internal path because it costs too much. Use when a user says "validate this payload", "pydantic vs dataclass", "BaseModel", "field_validator", "model_validator", "pydantic-settings", or when a request schema needs a clean error shape. Triggers on "ValidationError", "extra fields", "BaseSettings", "attrs", "dataclass slots", "parse this JSON safely".
compatibility: pydantic v2 (2.x) API specifically. v1 code (validator, root_validator, parse_obj_as, Config inner classes) does not apply and the v2 replacements are named explicitly.
metadata:
  version: "1.0"
---

# Python Data Modeling

Pick one tool per layer and use it deliberately: **pydantic at the boundary**
(untrusted input, config, serialisation contract) and **dataclasses in the
core** (internal, hot, already trusted). Using pydantic everywhere is a
measurable mistake; using it nowhere means you hand-roll validation and get it
wrong.

## The default split

| Layer | Use | Why |
|---|---|---|
| HTTP request, CLI args, config, file upload, queue message | **pydantic v2 `BaseModel`** | Untrusted input; you want coercion, defaults, and one good error shape |
| Domain objects, internal function args, DB rows | **`@dataclass(slots=True)`** (or frozen) | Already valid; no runtime cost; full type checker support |
| A small, totally fixed, memory-only shape | **`NamedTuple`** | Tuple semantics, named fields, cheap |
| Third-party library, heavy metaclass-based validation you would not write | **`attrs`** | Comparable to dataclasses with validators and converters, no pydantic overhead |

pydantic is a **boundary** tool. It is not "the way to model data in Python".

## pydantic v2 — the API that changed

v2 is a rewrite (Rust core). The v1 spellings are gone and code using them
either errors or silently misbehaves. Use these, not those:

| v1 (removed / different) | v2 |
|---|---|
| `@validator` | `@field_validator` (add `"after"` or `"before"` mode) |
| `@root_validator` | `@model_validator(mode="after" \| "before")` |
| `class Config:` | `model_config = ConfigDict(...)` |
| `parse_obj_as(T, data)` | `TypeAdapter(T).validate_python(data)` |
| `.dict()` | `.model_dump()` |
| `.json()` | `.model_dump_json()` |
| `.copy()` | `.model_copy(update=...)` (shallow by default) |
| `Field(..., regex=...)` | `Field(..., pattern=...)` |
| `Field(..., allow_mutation=False)` | `model_config = ConfigDict(frozen=True)` |
| `schema()` | `model_json_schema()` |
| `__fields__` | `model_fields` |

The type annotations are the schema. Use them directly; most custom
validation you write can be replaced by a type.

```python
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class User(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int = Field(ge=1)
    email: str = Field(max_length=254)
    signup_at: datetime
    tags: list[str] = Field(default_factory=list)   # NOT `= []` (shared mutable default)
    plan: Literal["free", "pro"] = "free"

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("must contain @")
        return v

    @model_validator(mode="after")
    def check_dates(self) -> "User":
        if self.signup_at.year < 2000:
            raise ValueError("signup_at looks wrong")
        return self        # AFTER validators return self; mutate or return
```

### Validators

- `@field_validator` is per-field. `@classmethod` is required under it in v2.
- `@field_validator("x", mode="before")` gets the raw input; use it to
  normalise before the type check (e.g. strip a prefix). `mode="after"`
  (the default) gets the already-coerced value.
- `@model_validator(mode="after")` validates the whole model and returns `self`.
  `mode="before"` gets the raw input as a dict/class — use it to normalise or
  require fields conditionally.
- `raise ValueError` (or `AssertionError`); pydantic turns it into a
  `ValidationError` entry. Raising a bare string or a random exception is a
  bug. `PydanticCustomError` is the type-safe way to emit a machine-readable
  error `code` the client can branch on.
- `Annotated` constraints keep the model tidy:
  `Port = Annotated[int, Field(ge=1, le=65535)]`.

### Errors

`ValidationError.errors()` is the shape to hand to a client — one entry per
problem, so you can return all of them at once (RFC 9457 field errors; see
`rest-api-implementation`):

```python
try:
    user = User.model_validate(payload)
except ValidationError as e:
    for err in e.errors():
        # {"type": "string_too_long", "loc": ("email",), "msg": "...", "input": ...}
        print(err["loc"], err["type"], err["msg"])
```

`loc` is a **tuple**, not a string — `("address", "zip")` for a nested model.
`e.errors()` includes the offending `input` and sometimes a `ctx`; do not
serialise the whole thing to a client, it can echo secrets back. Map it to
your own problem-details shape.

### Strict mode vs coercion

By default pydantic **coerces** in "lax" mode: `"5"` → `5`, `"true"` → `True`,
`datetime` from a Unix timestamp, and (in lax mode) a str→int coercion. That is
usually what you want for a JSON API where a client sends `"42"`.

Tighten at the boundary where coercion hides client bugs:

```python
model_config = ConfigDict(strict=True)          # no coercion anywhere on this model
id: int                                              # now "5" fails

# Or per-field / per-type:
id: StrictInt
value: Annotated[float, Field(strict=True)]
```

- Use **lax (default)** for JSON request bodies — a client sending `"42"` for
  an int is a client bug but not one worth a 422.
- Use **`strict=True` for config and for anything security-sensitive** (ids,
  booleans, money), where `"true"` silently meaning `1` matters.
- A common bug: `bool` in lax mode accepts `"false"`, `"no"`, `"off"`, `0`,
  `""`. In strict mode only a real bool passes. If a field is a bool, decide
  which you want and set it.
- `extra="forbid"` (reject unknown fields) is the right default for request
  models: a typo like `emial=` should 422, not silently vanish. `extra="ignore"`
  (the default) hides client bugs; `extra="allow"` lets unvalidated junk into
  your model.
- **Field aliasing** for a different wire name:
  `Field(alias="userId")`; populate by alias with
  `model_config = ConfigDict(populate_by_name=True)`, and
  `model_dump(by_alias=True)` to emit the alias. Getting this wrong
  (validating by field name but dumping by alias, or vice versa) is a classic
  "the API returns empty strings" bug.

### Settings

Config from the environment is a boundary too — validate it once at startup so
a bad env var fails fast, not at 3am on the first request that touches it.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str = "redis://localhost:6379"
    workers: int = Field(default=4, ge=1, le=64)
    debug: bool = False

settings = Settings()      # raises at import/startup on a missing/invalid var
```

`pydantic-settings` is a separate package in v2 (it was `BaseSettings` in the
pydantic v1 package). Env vars are matched case-insensitively; nested settings
use `__` (`db__host`). Secrets (`SecretStr`) are not printed in
`repr()`/`ValidationError` output — use them so a stack trace does not leak a
password. Instantiate the settings object once and inject it; do not re-read
`os.environ` throughout the app.

## When NOT to use pydantic internally

This is the advice most often skipped, and the one that matters at scale.

- **Validation runs on every construction.** A `BaseModel` instance is
  expensive to create relative to a `dataclass` (Rust-core validation, field
  descriptors, `__dict__`-backed, validation hooks). Building one per row in a
  100k-row loop is a real, measurable cost.
- **Model construction is not free in the way people assume.** Nested
  validation, `__init__` overrides, and computed fields all run on
  instantiation. In a hot path, the validation is pure overhead on data you
  already trust.
- **The pattern that works:** validate **once** at the edge (request body,
  config, queue message) into a pydantic model, then **convert to internal
  types** — a `@dataclass(slots=True)`, a plain class, or a `TypedDict` — and
  pass *that* through the core. The core is fast and fully typed; the boundary
  is safe.

```python
from dataclasses import dataclass, field

# Boundary: pydantic validates untrusted input once.
class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str
    qty: int = Field(ge=1, le=1000)

# Core: a plain slotted dataclass. Fast to build, immutable, no validation.
@dataclass(frozen=True, slots=True)
class Order:
    sku: str
    qty: int

def place(req: CreateOrderRequest) -> Order:
    return Order(sku=req.sku, qty=req.qty)     # one conversion, then cheap
```

Other cases where pydantic is the wrong tool: a per-frame video object, a
maths library's internal vector, anything created millions of times, a library
that should not force a dependency on pydantic. And the inverse: an internal
type that crosses a process boundary (message, file) **should** be validated
somewhere, even if the current producer is trusted.

## dataclasses vs attrs vs NamedTuple vs slots

**Use `@dataclass(slots=True)` for internal value objects.** It is stdlib, it
has zero runtime validation cost, mypy understands it perfectly, and
`slots=True` gives the memory/attribute win. `frozen=True` makes it hashable
and immutable:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)     # the default internal value object
class Order:
    sku: str
    qty: int
    tags: tuple[str, ...] = ()          # immutable default

@dataclass(slots=True)                  # mutable, for an entity you update
class Cart:
    items: list[str] = field(default_factory=list)   # not `= []`
```

- `slots=True` requires 3.10+. It means **no dynamic attributes** and no
  weakref unless declared; a base class without `slots` in the MRO silently
  cancels the saving for the whole hierarchy.
- A plain `= []` or `= {}` default is **shared across all instances** in a
  dataclass (it is a class attribute). Use `field(default_factory=list)`. This
  is a top-3 Python bug.
- `dataclasses.asdict()` is recursive and copies everything — for a big nested
  structure in a hot path, build the dict yourself.

**`NamedTuple`** for a small, fixed, memory-only shape: tuple semantics,
unpacking, cheap, hashable. It is immutable and indexable. But it has no
default-field machinery, no `__slots__` benefit beyond the tuple itself, and
field access is slower than an attribute on a slotted class.

**`attrs`** (`@define`, `@frozen`) when you want dataclass ergonomics plus
built-in validators/converters and better inheritance, without pydantic's
weight. It is a third-party dependency; for a plain value object dataclasses
are enough.

Pick **one** for internal objects. Mixing two in the same codebase for the
same purpose means every boundary needs a conversion.

## Gotchas

- **`Optional[X]` vs `X | None`**: pydantic treats them the same for
  validation, but a field annotated `X` with a `None` default still requires
  the caller to pass it unless it has a default. `x: int | None = None` is
  the way to make it optional.
- **A field with a mutable default and no `default_factory` raises
  `ValueError: mutable default <class 'list'> for field x is not allowed`** at
  class-creation time in a dataclass, and in pydantic v2 is a deep-copy
  workaround that masks the shared-mutable intent — prefer
  `default_factory`.
- **`model_dump()` returns Python objects; `model_dump(mode="json")` returns
  JSON-safe ones** (datetimes → ISO strings, UUID → str, Decimal → float or
  str). If you are about to `json.dumps(model.model_dump())` and hit
  `TypeError: Object of type datetime is not JSON serializable`, you want
  `mode="json"` or `model_dump_json()`. Passing a pydantic model straight into
  a framework's `JSONResponse` often serialises the pydantic-internal shape
  instead of your fields — `model_dump(mode="json")` is the safe handoff.
- **`model_copy(update=...)` does not validate the update** and is shallow.
  Mutating a copy can leave it in a state that `model_validate` would reject.
  For a validated change, re-validate: `Model.model_validate({**old, **patch})`.
- **A default that is a mutable model or a computed value is evaluated once**
  unless it is a `default_factory`. `Field(default_factory=datetime.now)`
  not `Field(default=datetime.now())` (the latter is frozen at import).
- **Serialisation performance:** `model_dump_json()` uses the Rust core and is
  much faster than `json.dumps(model_dump())`. For very large payloads, stream
  or use `orjson` at the edge, but keep pydantic's dump as the correctness
  reference.
- **`validate_python` vs parsing manually**: `Model.model_validate(obj)` checks
  types; `Model(**obj)` is looser and can mis-handle extra keys. Prefer
  `model_validate` at the boundary.
- **Serialization aliases must be symmetric.** A field with `alias="user_id"`
  and no `populate_by_name=True` cannot be built with `User(user_id=...)`; and
  `model_dump()` emits the field name, not the alias, unless
  `by_alias=True`. Set both sides together.
- **A pydantic model is not a DB model.** Do not use the same class for an
  API request and an ORM row with a lazy relationship; the validation and
  loading semantics fight each other.
