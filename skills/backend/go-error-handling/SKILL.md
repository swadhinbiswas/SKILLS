---
name: go-error-handling
description: Design Go error handling that callers can actually branch on - errors.Is and errors.As, wrapping with %w, sentinel errors vs custom typed errors, when a type carries data, and the boundary discipline between libraries and applications. Use when a user pastes an error, asks "how do I check for this error", "wrap vs return", "errors.Is vs errors.As", or when designing a package's error API. Triggers on "error handling", "err != nil", "%w", "errors.Is", "errors.As", "sentinel", "custom error", "panic vs error", "error chain", "unwrapping".
compatibility: Go 1.13+ error wrapping, 1.20 errors.Join and multiple %w, 1.21 min/max/clear. Standard library only (errors, fmt, os).
metadata:
  version: "1.0"
---

# Go Error Handling

Go errors are **values**, and the whole system is built on three primitives:
every error is an `error`, a wrapped error keeps a link to what it wrapped
(`%w`), and callers inspect with `errors.Is` / `errors.As`. Design an error API
around what the caller must *decide*, and the rest follows. The rule of thumb:
**a function returns an error; it does not log one and it does not swallow one.**

## Workflow

- [ ] 1. Decide what a caller must be able to branch on (retry? 404? bug?)
- [ ] 2. Make it a **sentinel** (`var ErrX = errors.New(...)`) or a **typed
      error** if it must carry data
- [ ] 3. At each layer that adds information, wrap with `fmt.Errorf("...: %w",
      err)` — `%w`, not `%v`
- [ ] 4. Keep every leaf error `errors.Is`-comparable; expose types for
      `errors.As`
- [ ] 5. Only the boundary (main, HTTP handler, CLI) logs, and it logs the
      **full chain** once
- [ ] 6. `panic` only for programmer bugs — never for expected failures

## The three primitives

```go
type error interface {
    Error() string
}
```

- **Return, don't log.** A library that logs *and* returns forces the caller
  to log again, producing the same failure twice at two levels with no
  structure. Return the error; let the top decide how to present it.
- **Wrapping preserves the chain** and adds a layer of context:

```go
// %w wraps (keeps the cause); %v does NOT (chain is broken).
fmt.Errorf("load config %s: %w", path, err)

// The caller still recognises the original:
errors.Is(err, os.ErrNotExist)   // true, through any number of %w layers
```

- **`errors.Is`** — is this error this sentinel? Unwraps the chain looking for
  an `==` match (or an `Is(error) bool` method).
- **`errors.As`** — is there an error of this *type* in the chain? Extracts it
  so you can read its fields.

```go
if errors.Is(err, sql.ErrNoRows) { ... }

var pathErr *os.PathError         // pointer to the concrete type
if errors.As(err, &pathErr) {
    log.Printf("bad path %s: %v", pathErr.Path, pathErr.Err)
}

var v *json.SyntaxError
if errors.As(err, &v) { ... v.Offset ... }
```

Both take a **pointer to a variable of the target type** (`&pathErr`), and
`As` requires that type to implement `error`. Use `errors.As` for typed
errors, `errors.Is` for sentinels. The second return value tells you if it
matched, so `errors.As(err, &e)` is the idiomatic `if`.

## Sentinel errors vs custom types

**Sentinel** when the caller only needs "did this happen, and which kind":

```go
var (
    ErrNotFound     = errors.New("not found")
    ErrConflict     = errors.New("conflict")
    ErrUnauthorized = errors.New("unauthorized")
)

func Get(id string) (User, error) {
    u, ok := store[id]
    if !ok {
        return User{}, ErrNotFound          // wrap to add detail, keep Is() true
    }
    return u, nil
}

// Caller:
if errors.Is(err, store.ErrNotFound) { ... }
```

**Custom typed error** when the caller needs **data** from the failure:

```go
type ValidationError struct {
    Field   string
    Problem string
}
func (e *ValidationError) Error() string {
    return fmt.Sprintf("field %q: %s", e.Field, e.Problem)
}

type RateLimitError struct {
    RetryAfter time.Duration
    Limit      int
}
func (e *RateLimitError) Error() string { return "rate limited" }

var err = &ValidationError{Field: "email", Problem: "invalid format"}
if errors.Is(err, sql.ErrNoRows) { ... }                     // no: different error
var ve *ValidationError
if errors.As(err, &ve) { fmt.Println(ve.Field) }              // yes: reads ve.Field
```

- A custom type is itself a sentinel if you export a value: `var
  ErrValidation = &ValidationError{}` is awkward; better to keep types and
  match with `As`, or define a package-level `var ErrValidation = errors.New`
  for the *category* and a type for the *detail*.
- Give a custom error an `Is(target error) bool` method when you want a
  sentinel that carries data, and an `As(any) bool` for the reverse. Then it
  behaves like both.
- **Rule of thumb:** if a caller only branches, use a sentinel. If a caller
  needs a field off the error to build a response, use a type. When in doubt,
  both: a sentinel wrapped around a typed error.

## Wrapping discipline — the boundary rule

The rule that keeps error trees readable:

- **Low-level packages** return precise, unwrapped errors (`os`, `sql`, `json`
  already do). Do not add narration to `io.ReadFull`.
- **Each layer up wraps with the operation it was doing and the identifiers
  involved** — exactly enough for a human reading a log to know where it
  broke: `fmt.Errorf("query orders for customer %s: %w", custID, err)`.
- **The boundary** (`main`, an HTTP handler, a CLI `Run`) does exactly one
  thing with the error: turn it into a status/exit and log the **whole chain**
  once, at the top.

```go
func main() {
    if err := run(); err != nil {
        slog.Error("fatal", "err", err)   // logs full chain once
        os.Exit(1)
    }
}
```

- **Do not wrap the same error at every level with the same words.** "error:
  error: error: EOF" is what happens when layers add nothing. Each wrap should
  add information a reader does not already have (which id, which step).
- **Always wrap with `%w` when the caller might need to `Is`/`As` through your
  layer.** `%v` silently severs the chain and turns a typed failure into an
  opaque string. This is the most common Go error bug.
- **`errors.Join` (Go 1.20+)** merges several errors into one whose `Is`/`As`
  match any of them, and whose `Error()` is the errors separated by newlines.
  Use it when failing fast would discard real information (validating several
  fields, closing several resources). `errors.Is(joined, target)` is true if
  any component matches.

## `errors.Is` / `errors.As` and the `Is`/`As` methods

For a custom error to participate, give it the methods:

```go
// All methods on *ErrCode: mixing value and pointer receivers on one type is
// a bug (see Gotchas).
type ErrCode struct{ Code int }

func (e *ErrCode) Error() string { return fmt.Sprintf("code %d", e.Code) }

func (e *ErrCode) Is(target error) bool {   // sentinel-style matching by value
    te, ok := target.(*ErrCode)
    return ok && te.Code == e.Code
}

func (e *ErrCode) As(target any) bool {    // extract the int without knowing ErrCode
    if p, ok := target.(**int); ok {
        *p = e.Code
        return true
    }
    return false
}

var NotFound = &ErrCode{Code: 404}

// Caller: matches like a sentinel, extracts like a type, or digs out the code.
if errors.Is(err, NotFound) { ... }
var ec *ErrCode
if errors.As(err, &ec) { fmt.Println(ec.Code) }
var code int
if errors.As(err, &code) { fmt.Println(code) }
```

## panic / recover — honest rules

`panic` is for **programmer bugs and truly unrecoverable states**, not for
expected failures. In Go, an unrecovered `panic` in **any** goroutine kills
the whole process (there is no per-goroutine isolation), so a panic in library
code is a denial of service.

**Right to panic:**

- Programmer error / broken invariant: index out of range you should have
  checked, nil deref that can only happen if a contract is violated, "this
  should be impossible".
- A framework's or library's internal invariant (`must` functions, `Must*`
  constructors, `template.Must`).
- At startup, if configuration is invalid and there is no way to continue.

**Wrong to panic:**

- Validation failure, missing input, "file not found", a downstream 500, a
  user-supplied bad ID, a timeout. These are `error` returns.
- Anything a caller could reasonably want to handle. If you `recover` in
  production to turn a panic into a 500, you have a bug that should be fixed,
  not a control-flow feature.

**`recover` rules:**

```go
defer func() {
    if r := recover(); r != nil {
        log.Error("panic", "v", r, "stack", string(debug.Stack()))
        err = fmt.Errorf("internal: %v", r)   // optionally convert to error
    }
}()
```

- `recover` only works when called **directly in a deferred function of the
  panicking goroutine**. It does not cross goroutines; recovering in the
  parent of a `go func()` that panicked does nothing (the child is already
  dead and the process is going down).
- Recovering and continuing after a panic leaves global state possibly corrupt
  (a half-updated invariant, a held mutex that will never be released). Only
  recover at a boundary where you can discard the partial work and return a
  clean error — a request handler, a worker processing one job.
- **Do not use `recover` as a substitute for a mutex or for error returns.**
- A deferred `recover` that returns a *named* error result is the standard way
  to turn a panic into an error at a boundary — but note you must have named
  the return: `func f() (err error) { ... }`.
- `panic(nil)` is special: since Go 1.21, it produces a `*runtime.PanicNilError`
  (a non-nil value) instead of making `recover()` return nil. Code that
  assumed `recover() == nil` meant "no panic" is safe on 1.21+, but a bare
  `recover()` with no second check is still the pattern to avoid.

## The `error` interface traps

- **Nil interface, non-nil underlying pointer.** The classic Go nil trap:

```go
type MyErr struct{}
func (e *MyErr) Error() string { return "boom" }

func bad() error {
    var p *MyErr = nil
    return p               // error interface is NON-nil (type=*MyErr, value=nil)
}
if bad() != nil { ... }   // true! but Error() would nil-deref
```

  Returning a typed nil pointer as `error` gives a non-nil interface. Avoid
  returning `*MyErr` from a function that can return "no error"; return a
  plain `error` variable and only assign a non-nil concrete value, or return
  the value type (not a pointer) as the error.
- **`errors.New` vs `fmt.Errorf`:** `errors.New("x")` for a constant sentinel;
  `fmt.Errorf` for anything with a `%w` or formatting.
- **Error strings are lowercase and un-punctuated** by convention
  (`"connection refused"`, not `"Connection refused."`), because they are
  frequently embedded in a larger message. No trailing newline, no "error:"
  prefix, no user input in them.
- **Never put a secret or a full SQL statement in an error** that could reach a
  client. Errors get logged and returned; treat them as output.
- **`fmt.Errorf` with no `%w` on a wrapped error** breaks the chain. If you
  want to keep the type but not expose the inner text to a user, keep `%w` for
  the chain and sanitise only at the client boundary.
- **Comparing errors with `==` only works for sentinels you exported.** It
  fails for wrapped errors (`err == ErrNotFound` is false once wrapped) and
  for typed errors. Use `errors.Is`.

## Testing error behaviour

```go
func TestGet_NotFound(t *testing.T) {
    _, err := Get("missing")
    if !errors.Is(err, ErrNotFound) {
        t.Fatalf("want ErrNotFound, got %v", err)
    }
}

func TestGet_WrapsValidation(t *testing.T) {
    _, err := Parse("")
    var ve *ValidationError
    if !errors.As(err, &ve) || ve.Field != "name" {
        t.Fatalf("want ValidationError{name}, got %#v", err)
    }
}
```

Test the *contract* the callers depend on (`Is` on the sentinel, `As` on the
type, field values), not the exact message string. Asserting on
`err.Error() == "..."` is brittle and will be broken by any reword.

## Gotchas

- **`%w` only wraps one error, or several as of Go 1.20.** `fmt.Errorf("a: %w
  b: %w", e1, e2)` produces a multi-wrap whose `Is` matches either. Before
  1.20, only one `%w` was allowed and a second made the verb print `%!w(...)`.
- **`errors.Is(err, nil)` is true only when `err` is nil.** Don't rely on it to
  "is there a real error"; it is just equality through unwrapping.
- **`errors.As` panics if the target is not a pointer to a type implementing
  `error` (or to an interface).** `errors.As(err, ve)` (no `&`) panics; use
  `&ve`.
- **Wrapping a `context` error:** return `ctx.Err()` unwrapped or wrapped —
  `errors.Is(err, context.Canceled)` must still work. Never replace a
  `context.Canceled`/`DeadlineExceeded` with a generic "operation failed";
  callers and retry logic check for them specifically.
- **Do not compare error strings** for control flow; compare sentinels or
  types. Strings change and are not unique.
- **A returned `nil` error with a non-nil typed value is a bug** (the nil
  pointer trap above) and is exactly what static analysis and careful returns
  prevent.
- **Custom errors should be pointer receivers** so `As` with a `*T` target
  matches, consistently. Mixing value and pointer receivers on the same type
  means only one form satisfies `error`.
