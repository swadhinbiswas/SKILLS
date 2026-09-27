---
name: rust-ownership-patterns
description: Use Rust's ownership, borrowing, and lifetimes to make real code pleasant - newtype, typestate, builder, and the Cow/Arc/&str tradeoffs, plus the idiomatic fixes for the borrow checker battles everyone hits (split borrows, NLL, interior mutability, iterators vs indexes). Use when writing or refactoring Rust, when hitting "cannot borrow ... as mutable more than once", "borrowed value does not live long enough", or when a user asks about lifetimes, Rc/Arc/Cow, or designing an API that is pleasant to call. Triggers on "borrow checker", "lifetime", "newtype", "typestate", "builder pattern", "Rc vs Arc", "Cow", "clippy", "ownership".
compatibility: Rust 1.70+ idioms (OnceLock, let-else 1.65, impl Trait in return position). Edition 2021+.
metadata:
  version: "1.0"
---

# Rust Ownership Patterns

Ownership is a tool, not a tax. Once you stop fighting the borrow checker and
start using it, the type system stops whole categories of bug (use-after-free,
data races, double-free) at compile time, and you get APIs that make illegal
states unrepresentable. This skill is the practical guide: the patterns that
make ownership pleasant, and the borrow-checker battles with their idiomatic
solutions.

## Workflow

- [ ] 1. Model the domain with the type system: enums for states, newtypes
      for units, typestate for phase transitions
- [ ] 2. Take `&self` for reads, `&mut self` for changes, `self` for consumes —
      and mean it
- [ ] 3. Prefer **borrowing (`&T`, `&str`) over cloning** in signatures; clone at
      the boundary where ownership must transfer
- [ ] 4. Reach for `Cow` / `Rc` / `Arc` only when a borrow will not do
- [ ] 5. When the borrow checker blocks you, apply the split-borrow / NLL /
      interior-mutability fix rather than cloning everything
- [ ] 6. `cargo clippy -- -D warnings` and `cargo fmt` before you call it done

## The ownership mental model (the useful 20%)

- Every value has exactly one owner. When the owner goes out of scope, the
  value is dropped.
- `&T` is a shared borrow (many readers, no mutation). `&mut T` is a unique
  borrow (one writer, no other readers). The borrow checker enforces: many
  `&` **or** one `&mut`, never both at once, and never two `&mut`.
- `T` moves by default. Assignment and passing by value **move** the value; the
  old binding is dead. This is why "use of moved value" is the most common
  error — you used a variable after handing it away.
- Borrowing lasts until the last use of the reference (this is NLL, non-lexical
  lifetimes, stable since Rust 2018). A borrow does not have to scope to the
  end of the block.

Signatures, chosen deliberately:

```rust
fn read(&self) -> &str        // borrow: caller keeps their copy
fn consume(self) -> String    // take ownership: caller gives it up
fn mutate(&mut self)          // unique borrow: caller keeps it, you change it
```

Take `self` when the function stores the value or transforms it into something
else; take `&self` for read-only access. Taking `&self` and cloning internally
when you could take `self` is usually the wrong instinct.

## Newtype: make illegal units impossible

`struct Meters(f64)` is a distinct type from `struct Feet(f64)`. You cannot
add them, and the compiler rejects the mistake. This is the highest-value,
lowest-effort pattern in Rust.

```rust
#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
struct Meters(f64);
#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
struct Seconds(f64);

fn accelerate(v: Meters, t: Seconds) -> Meters {
    Meters(v.0 + 9.81 * t.0)
}
// accelerate(Meters(1.0), Seconds(1.0));
// accelerate(Seconds(1.0), Seconds(1.0));  // compile error: expected Meters
```

- Implement `From`/`Into` for the common conversions so the ergonomic
  constructor and error conversion are one line.
- Newtype an **id** so you cannot pass a `UserId` where a `OrderId` is
  expected. Use a macro to generate the boilerplate (`paste` + `new_unchecked`,
  or the `nonzero` crate for ids that must not be zero).
- Newtype an **unparsed/extracted** value: a function that must run a query
  returns `User`; the raw `UserRow` never escapes the data layer. A validated
  newtype in the type system is a proof the validation ran.
- Newtype a **unit-bearing integer** so `set_timeout(5000)` fails; it has to be
  `set_timeout(Millis(5000))`. `u32` millis vs `u64` micros vs a `Duration`
  are the classic confusion.

## Typestate: encode state transitions in the type

If a value's validity depends on what you have done to it, make the type depend
on it. You cannot use a `Connection` before it is authenticated, because a
pre-auth `Connection` is a *different type* you do not have.

```rust
struct Connection<S> { stream: TcpStream, _state: PhantomData<S> }
struct Disconnected;
struct Connected;

impl Connection<Disconnected> {
    fn connect(addr: &str) -> Result<Connection<Connected>, Error> {
        let stream = TcpStream::connect(addr)?;
        Ok(Connection { stream, _state: PhantomData })
    }
}
impl Connection<Connected> {
    fn query(&mut self, q: &str) -> Result<Rows, Error> { /* ... */ }
}

// let mut c = Connection { ... };  // cannot construct: fields are private
// Connection::connect("...") -> Connection<Connected>, which HAS .query()
```

- Typestate removes whole classes of runtime checks ("am I connected?") and
  makes the API guide the user. Use it for linear workflows: build steps,
  pipelines, protocol handshakes, request/response sessions.
- The cost: more types, generics that can bloat compile times, and a harder
  mental model. Reach for it when the state machine has a real ordering
  constraint, not for every struct. A simpler alternative is a runtime `state`
  field with a `check_transition` method — typestate is the compile-time
  version of that, worth it when the invalid transitions are likely and
  expensive to debug.
- `PhantomData<S>` ties the generic to the struct without owning an `S`; use
  `PhantomData<fn() -> S>` to indicate the type parameter is not owned.

## Builder: many optional fields without an unwieldy constructor

Rust has no keyword/named arguments, so a struct with many optional fields
needs a builder.

```rust
#[derive(Default)]
struct Server { host: String, port: u16, workers: usize, tls: bool }

struct ServerBuilder { inner: Server }

impl ServerBuilder {
    fn new() -> Self { Self { inner: Server { host: "localhost".into(), port: 80, ..Default::default() } } }
    fn host(mut self, h: impl Into<String>) -> Self { self.inner.host = h.into(); self }
    fn port(mut self, p: u16) -> Self { self.inner.port = p; self }
    /// Consuming `self` and returning `Self` lets calls chain.
    fn build(self) -> Server { self.inner }
}
```

- Every setter takes `mut self` and returns `Self` by value. This is the
  idiomatic consuming builder; it avoids `&mut self` (which fights
  borrowck when you chain) and avoids interior mutability.
- If you want the builder reusable, use `&mut self` — but then calls do not
  chain, and that is the tradeoff.
- **Do not build both a `new()` with many args and a builder** for the same
  struct; pick one. If fields are mostly required, a `new(required...)` plus
  `with_optional(mut self, ...)` chain is simpler than a full builder.
- Validate in `build()` and return `Result<Server, BuildError>` rather than
  `panic!`. Builders that panic on a bad port are a footgun.

## Cow, Arc, Rc, and the reference strategy

Choosing how a struct *holds* a string or collection is the most common
performance design decision:

| Type | Holds | Cost | Use when |
|---|---|---|---|
| `&str` / `&[u8]` / `&T` | Borrow | Free, but the **borrower** keeps the data alive (lifetime plumbing) | The common case: a function or field that borrows |
| `String` / `Vec<T>` / `Box<T>` | Own | One allocation; moving is cheap | The struct must outlive the source |
| `Cow<'a, T>` | Borrow **or** own, decided at runtime | Enum internally; no clone when borrowed | A field that is usually borrowed but sometimes must own (normalising line endings, a config default) |
| `Rc<T>` | Shared, **single-threaded** | Refcount, non-atomic; `Rc` is `!Send`/`!Sync` | Graph/tree structures, parent pointers, intra-thread sharing |
| `Arc<T>` | Shared, **multi-threaded** | Atomic refcount; more expensive | `Arc<Mutex<T>>` across threads |

```rust
use std::borrow::Cow;

// Usually borrowed, owned only if we must rewrite it.
fn normalise(input: &str) -> Cow<'_, str> {
    if input.contains('\r') {
        Cow::Owned(input.replace('\r', ""))     // allocated, we own it
    } else {
        Cow::Borrowed(input)                     // zero-copy
    }
}

// Shared tree nodes, single thread (Rc is not Send).
struct Node { parent: Option<Rc<Node>>, children: RefCell<Vec<Rc<Node>>> }

// Shared across threads; combine with a lock.
let shared: Arc<Mutex<Config>> = Arc::new(Mutex::new(Config::default()));
```

- **`&str` in a struct means every user must keep the source string alive**,
  and the struct's lifetime is tied to it. For a type that is stored, sent over
  a channel, or put in a struct that outlives the call, use `String` (own) or
  `Cow<'static, str>` — and the `'static` documents "I do not borrow."
- **`Rc` is not `Send`.** Putting an `Rc` in something sent to another thread
  is a compile error, which is the compiler protecting you. Use `Arc` there.
- **Do not use `Rc<RefCell<T>>` / `Arc<Mutex<T>>` as a first resort.** Reach
  for `&mut` or ownership. Interior mutability is for genuinely shared,
  cyclic, or callback-driven graphs where the borrow checker cannot see the
  ownership order.
- **Clone a `String` at the boundary, borrow in the middle.** A
  `fn process(&str)` called in a loop is fine; a `fn process(String)` in a hot
  loop is an allocation per call. Take `&str`, and only the function that
  *stores* the data allocates.

## Borrow-checker battles and their idiomatic solutions

These are the four you will actually hit.

### 1. "cannot borrow `x` as mutable more than once"

Two `&mut` to the same value, often via a method that iterates and calls a
method that also borrows mutably.

```rust
// ERROR: `v` borrowed mutably by `v.push`, then again by `v.len`.
for x in &v { v.push(f(x)); }        // push needs &mut v; &v borrows it

// FIX 1: split the borrow with fields, destructure first.
let Node { children, weight, .. } = &mut node;   // disjoint fields, both &mut ok
for c in children.iter_mut() { c.update(*weight); }

// FIX 2: index, which the borrow checker treats as disjoint.
let arr = &mut self.items;
let i = arr.iter().position(...).unwrap();
arr[i] = new;                        // sequential, not overlapping

// FIX 3: take by value when you are going to consume anyway.
fn drain_all(&mut self) -> Vec<T> { std::mem::take(&mut self.items) }
```

### 2. "borrowed value does not live long enough"

A reference (often a `&self` field, a closure, or an iterator) outlives the
data it points at. Usually a temporary:

```rust
// ERROR: `s` is a temporary String; `first` borrows it and dies.
fn bad() -> &str { let s = String::from("hi"); &s }

// FIX: return an owned String, or take the input by reference.
fn good() -> String { String::from("hi") }
```

In structs, the fix is usually **owning the data** (`String` instead of
`&'a str`) rather than adding a lifetime parameter. Adding `<'a>` to a struct
threads a lifetime through every user; owning is almost always simpler.

### 3. Closures capturing while you also mutate

```rust
// ERROR: `items` borrowed by `filter` closure, then `push` needs &mut.
items.retain(|i| i.valid());          // retain takes &mut self; the closure
items.push(new_item);                 // re-borrows — actually OK with NLL
```

Usually NLL already accepts this. When it does not, prefer a method that takes
`&mut self` and does both in one pass (`retain_mut`, `sort_by`, `dedup`) over
fighting the closure. For iterator adaptors, `.filter(...)` borrows the
iterator, so do not also call a `&mut` method on it in the same expression —
bind to a variable first.

### 4. Recursive data structures (linked lists, trees, graphs)

```rust
enum List {
    Cons(i32, Box<List>),      // Box gives indirection: finite size
    Nil,
}
```

A bare `List { next: List }` is infinitely sized and rejected. Wrap the
recursive field in `Box` (single ownership), `Rc` (shared), or `&'a` (borrow,
implying an acyclic graph). Choose based on ownership: `Box` for a tree, `Rc`
for a graph with shared substructure.

## Gotchas

- **Cloning to satisfy the borrow checker is a smell, not always wrong.** In a
  hot path or a `Clone` on something expensive, restructuring is right. In a
  setup path, `clone()` is fine and clearer. Do not contort code to avoid a
  clone that does not matter.
- **`#[derive(Clone)]` is not free** — deep-derive `Clone` on a big struct
  clones the whole tree. Implement `Clone` manually or use `Rc`/`Arc` for the
  big inner value if clones are frequent.
- **The borrow checker is right about aliasing.** If you think you need
  `Rc<RefCell<T>>`, first check whether restructuring (split borrows, take by
  value, reorder) works. Interior mutability hides the ownership question
  rather than answering it, and the runtime `RefCell` panic
  (`already borrowed: BorrowMutError`) is the checker returning later.
- **`&mut self` in a trait method prevents two simultaneous mutable borrows.**
  If you need interior mutability across a shared reference, `&self` + a
  `Mutex`/`Cell` is the escape — use `Cell` for `Copy` (no locking), `RefCell`
  for single-threaded non-`Copy`, `Mutex`/`RwLock` for `Send`.
- **`.clone()` on a `&T` where you wanted an owned `T` is often `to_owned()`
  or `to_vec()`.** For `&str`→`String` it is `.to_owned()`; for `&[T]`→`Vec<T>`
  it is `.to_vec()`.
- **Unnecessary `.clone()` warnings come from `clippy::redundant_clone`**, but
  clippy is sometimes wrong; the borrow checker is right about `&mut`. Do not
  contort code to silence a lint that removed a correctness guarantee.
- **`impl Trait` in argument position** (`fn f(x: impl Trait)`) is sugar for
  generics; it is fine. **`impl Trait` in return position** hides the concrete
  type, which can hurt if the caller needs to name it (store it in a struct).
- **Prefer `iter().map(..).collect()` to manual `for` + `push`** for building
  collections — it reads better and usually compiles to the same thing. But
  do not contort a loop into an iterator chain if it is clearer as a loop.
- **`unwrap()` / `expect()` in library code is a bug**; in tests and examples
  it is fine. `expect("invariant: ...")` with a reason is acceptable when the
  condition is genuinely unreachable.
- **Clippy's pedantic lints are opinions, not laws.** `#![deny(clippy::all)]`
  plus a reviewed `#[allow(...)]` is fine; blindly denying `pedantic` fights
  idiomatic code.
