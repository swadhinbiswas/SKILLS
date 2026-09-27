---
name: rust-error-handling
description: Pick and use the right Rust error strategy - thiserror for library error enums with #[source] chains, anyhow for application/binary error context, eyre for reportable application errors, converting between them, and the modern gotchas around `?`, Box<dyn Error>, and trait objects. Use when designing a crate's error API, when a user asks "thiserror vs anyhow", "add context to an error", "define a custom error", or when fixing "the ? operator", "source()", "downcast". Triggers on "anyhow", "thiserror", "eyre", "Box<dyn Error>", "error chain", "context()", "From impl for errors", "Result<T, E>".
compatibility: thiserror 1.x and 2.x, anyhow 1.x, eyre 0.6. `#[error(transparent)]` and the `source` field are thiserror spellings. Prefer 2.x; check features in Cargo.toml.
metadata:
  version: "1.0"
---

# Rust Error Handling

Rust's default is `Result<T, E>` with **no** implicit context: a `?` either
returns the exact error or converts it. Libraries should let callers decide
what an error *means*, so a library defines a concrete error enum; binaries add
human-readable context as it propagates up. The modern split is
**thiserror for libraries, anyhow (or eyre) for applications**.

## The decision

| The crate is… | Use | Why |
|---|---|---|
| A **library** others depend on | `thiserror` + a concrete `enum Error` | Callers can `match` on variants; the type is part of your API; no `dyn` dispatch, no surprise |
| A **binary / service / CLI** | `anyhow::Result` | You only need "did it fail, and what do I tell the operator"; ad-hoc context with `.context()` is the point |
| A **binary that needs a `Report` with spans/backtraces** | `eyre` | Like anyhow, plus optional `SpanTrace`/location capture; opt-in via the `track-caller` feature |
| Anything that must be **`no_std`** | `core::error::Error` + a hand-written enum | `thiserror` works, but `std::error::Error` is unstable to implement manually on stable — see Gotchas |

Rule of thumb: **libraries never use `anyhow`/`Box<dyn Error>` in a public
signature.** That erases the error type and forces every caller to downcast. A
library's `Error` enum is a public API you version; treat it as one.

## thiserror: library errors

```toml
[dependencies]
thiserror = "2"
```

```rust
use std::num::ParseIntError;
use std::io;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum Error {
    #[error("could not read config from {path}")]
    ReadConfig {
        path: String,
        #[source]                         // the cause; shows in the chain
        source: io::Error,
    },
    #[error("config value {key:?} is invalid")]
    InvalidValue {
        key: String,
        reason: &'static str,            // data on the variant, no source
    },
    #[error("could not parse port")]
    ParsePort(#[from] ParseIntError),    // #[from] generates a From impl
    #[error("database unavailable")]      // unit variant: a category
    Unavailable,
}

pub fn load(path: &str) -> Result<Config, Error> {
    let text = std::fs::read_to_string(path).map_err(|source| {
        Error::ReadConfig { path: path.to_string(), source }
    })?;
    let port: u16 = text.trim().parse()?;   // ? uses the #[from] From impl
    if port == 0 {
        return Err(Error::InvalidValue { key: "port".into(), reason: "must be non-zero" });
    }
    Ok(Config { port })
}
```

The spellings that matter:

- `#[error("...")]` — the `Display` string. Use `{field}` interpolation
  (thiserror 1.x/2.x both support `{}` in the format string for fields);
  `.0`/`.1` for tuple fields. Do **not** put the source in the message by hand
  — `#[source]` handles the chain.
- `#[source]` — marks the field as the cause. `source()` returns it, and
  `anyhow`/`eyre` print the full chain. Use `#[from]` *instead* of `#[source]`
  when you also want a generated `From` impl.
- `#[from]` — generates `impl From<T> for Error`. You get one `From` per
  variant, so two variants cannot both `#[from]` the same type (a coherence
  conflict). If you need to distinguish two `io::Error`s, use `#[source]` and
  wrap manually.
- `#[error(transparent)]` — forwards `Display`, `source()`, and the exit code
  to the wrapped error, with no added message. Use for a variant that is
  purely "pass the cause through" (e.g. wrapping a lower-layer error enum).
- **Unit variants** (`Unavailable`) are fine for a category with no data.
  Callers `match` on them.

Rule: **a library error enum should let a caller exhaustively match** the
interesting cases. If your enum has 20 variants and a caller must read the
`Display` string to decide, the design is wrong.

## anyhow: application errors and context

```toml
[dependencies]
anyhow = "1"
```

```rust
use anyhow::{Context, Result, bail, ensure, anyhow};

fn load_config(path: &str) -> Result<Config> {          // note: anyhow::Result
    let text = std::fs::read_to_string(path)
        .with_context(|| format!("reading config {path}"))?;
    let port: u16 = text.trim().parse()
        .context("port is not a number")?;
    ensure!(port > 0, "port must be non-zero, got {port}");   // returns Err
    if port == 0 { bail!("port must be non-zero"); }           // early return
    Ok(Config { port })
}

fn main() -> Result<()> {                 // one line, the whole binary
    let cfg = load_config("app.toml")?;  // ? converts to anyhow::Error
    run(cfg)?;
    Ok(())
}
```

- **`.context(msg)` / `.with_context(|| ...)`** add a layer above the error
  without losing it. `.with_context` takes a closure so the string is only
  built if there *is* an error — use it when formatting is non-trivial.
  `.context("reading config")` on a *static* string is fine and cheaper.
- The printed chain is:
  ```
  Error: reading config app.toml
  Caused by:
      0: No such file or directory (os error 2)
      1: reading config app.toml
  ```
  (roughly — the outer message plus each source). This is what a human
  operator wants; a library should not produce it.
- `bail!` = early `return Err(anyhow!(...))`. `ensure!(cond, ...)` =
  `if !cond { bail!(...) }`. Both are macros over `anyhow!`.
- `anyhow!` creates an ad-hoc error with no type. Fine in a binary; do not
  leak it out of a library function.
- **`anyhow::Result` in a public library signature is a mistake.** It
  erases the type, forces callers to `downcast`, and couples your library to
  anyhow. Use `Result<T, YourError>`.

## eyre: like anyhow, with reports

```toml
eyre = { version = "0.6", optional = true }
```

Drop-in for `anyhow` (`eyre::Result`, `.wrap_err(...)` instead of
`.context(...)`, `?` from `WrapErr`). Add it when you want a `Report` that can
carry a `SpanTrace`/location for error reporting. Feature flags
(`auto-install`, `track-caller`) change the behaviour; read the crate's
feature docs before enabling. For a service that reports errors to a tracker,
`eyre` is the better `anyhow`.

## The `?` operator and trait objects — the real gotchas

- **`?` on `Result<T, E>` needs `E: Into<YourError>`.** For a
  thiserror `#[from]`, that `From` impl exists. Without it, `?` fails to
  compile — the fix is a `From` impl, not a `.map_err` at every site.
- **`Box<dyn std::error::Error>` in a signature** is legal and compiles, but
  it means the caller must `downcast_ref`/`downcast` to learn anything, and
  you lose the ability to match variants. Acceptable in a **binary**; avoid in
  a **library** signature. If you must in a library, document that callers
  should downcast to your concrete type, and expose the concrete type publicly
  so they can.
- **`Box<dyn Error + Send + Sync + 'static>`** is the form required when the
  error crosses a thread boundary (`tokio::spawn`, a `std::thread` closure
  result, an `anyhow` in a trait object). `Box<dyn Error>` alone is not
  `Send`, so `anyhow::Error` (which is `Send + Sync`) usually is. The exact
  bound matters when a generic function needs the error to cross threads.
- **Downcasting**:
  ```rust
  if let Some(io) = e.downcast_ref::<std::io::Error>() { /* ... */ }
  if let Some(my) = e.downcast_ref::<mycrate::Error>() { /* ... */ }
  let concrete = e.downcast::<std::io::Error>();   // consumes, returns Result<io, Error>
  ```
  `downcast` requires the target to implement `Error + 'static`.
- **`impl Error for MyError` is still unstable** (`std::error::Error` cannot
  be implemented manually on stable without `thiserror`). That is exactly why
  `thiserror` exists — it is a proc-macro that writes the impl. Do not try to
  hand-write `impl std::error::Error` on stable; it will not compile.
- **A trait method's error type** must be fixed in the trait. If a trait's
  methods can fail with different concrete types per implementor, either use
  `anyhow::Error` (and accept the coupling) or define an associated error type
  and a `From` bound. Do not return a different `E` per impl without declaring
  it in the trait.
- **Converting a library error into an anyhow error** happens automatically:
  `anyhow::Error: From<E> where E: std::error::Error + Send + Sync + 'static`.
  A thiserror enum satisfies this, so `.context(...)` works on a library error
  directly. If your error is **not** `Send + Sync` (e.g. it holds an
  `Rc`), `anyhow` cannot take it — this is a real constraint, and a reason to
  use `Arc` in your error types.

## Sources and context chains — the model

An error has two parts: a `Display` message for humans and a `source()` chain
for machines. Build both:

- **Library**: define the enum with `#[source]`, so `source()` walks the real
  cause. Wrap lower-layer errors; never re-format them into a string (that
  destroys the chain).
- **Binary**: add `.context(...)` at each step that a human would want
  described. The innermost error is the technical cause; each context layer
  says what the program was trying to do.
- **Do not duplicate the source in the `Display` string** ("failed to read
  config: No such file") *and* keep `#[source]` — `anyhow` prints both and you
  get the message twice. The `#[error("could not read config from {path}")]`
  message describes the *operation*; the source is the *cause*.

## Gotchas

- **Do not use `anyhow` in a library's public API.** It is the single most
  common Rust error-handling mistake and it is hard to undo once published.
- **Do not return `String` as an error type** in a library. You lose
  `source()`, matching, and the ability for a caller to distinguish cases.
- **`#[error(transparent)]` on a variant that wraps a multi-error** still
  forwards `source()`, but it adds no context — if you want context, write the
  message and keep `#[source]`.
- **An error enum with a `#[from]` for `std::io::Error` and another for a
  different type that is *also* `io::Error` via a newtype** works, but two
  `#[from]` variants with the same source type do not compile (conflicting
  `From` impls). Use `#[source]` + manual wrapping in that case.
- **`unwrap()` in a `main` is fine; in a library it is a bug.** A panic in a
  library called from a server aborts that request or the process; return
  `Result`.
- **`expect("...")` is better than `unwrap()`** because the message survives,
  but it is still a panic. Only for genuine invariants.
- **Error enums are non-exhaustive in practice** as you add variants. Adding
  one is technically a breaking change for a downstream `match` without a
  `_` arm; mark the enum `#[non_exhaustive]` if you plan to grow it.
- **`thiserror` derive generates `Display` and `Error`; it does not generate
  `From` unless you add `#[from]`.** Forgetting `#[from]` is why `?` stops
  compiling and you reach for `.map_err(to_string)`.
- **Panic in a `Drop` impl during unwinding** causes a double panic and aborts
  the process. `Drop` must not panic.

## Quick decision checklist

- [ ] Is this a **library**? Concrete `enum` + `thiserror`, `From` impls,
      `#[source]` chain, no `anyhow` in signatures.
- [ ] Is this a **binary**? `anyhow::Result` / `eyre::Result`, `.context()` at
      each meaningful step, one `main` that reports and exits non-zero.
- [ ] Does a caller need to **match** on this failure? It must be a typed
      variant, not a string — otherwise use a typed error, not `anyhow`.
- [ ] Does the error cross a **thread**? It must be `Send + Sync + 'static`;
      use `Arc`, not `Rc`, inside it.
- [ ] Is `impl std::error::Error` being hand-written? Use `thiserror` — it is
      not implementable manually on stable.
