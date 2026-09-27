---
name: typescript-type-safety
description: Type-level correctness that scales past the basics - discriminated unions with exhaustive never checks, avoiding any, narrowing with type guards, satisfies vs as, generic constraints, and validating untrusted input at the boundary because types erase at runtime. Use when modelling a state machine, an API response, or an event stream, or when a user says "how do I model this union", "exhaustiveness", "should I use as or satisfies", "type is wrong at runtime", or when casting creeps into a codebase. Triggers on "any", "as const", "satisfies", "never", "type guard", "discriminated union", "narrowing", "type-safe", "unvalidated JSON".
compatibility: TypeScript 5.x. satisfies is 4.9+; const type parameters 5.0; NoInfer 5.4. Verify the installed tsc version with tsc --version.
metadata:
  version: "1.0"
---

# TypeScript Type Safety

Types in TypeScript **erase at runtime**. They are a compile-time tool plus a
documentation and editor aid; a `User` from `fetch().json()` is a *claim*, not
a fact. The way to be actually type-safe is two-layered: model the domain with
precise types (discriminated unions, no `any`), and **validate untrusted data
at the edge** before it enters the typed world. This skill covers the types;
see the runtime boundary section for the validation.

## Workflow

- [ ] 1. Model states as a **discriminated union** with a literal `type`/`kind`
      field
- [ ] 2. Handle every variant with an `assertNever`/`never` return so the
      compiler catches a missed case
- [ ] 3. Keep `any` out; use `unknown` at the boundary and narrow
- [ ] 4. Use `satisfies` to check a shape **without widening it**; reserve `as`
      for genuine downcasts you can justify
- [ ] 5. Validate at the edge (schema) and let types flow inward
- [ ] 6. Turn on `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`

## Discriminated unions: the workhorse

A union of object types sharing a **literal discriminant** field is
discriminated. `switch` on it is checked, and adding a variant produces a
compile error at every `switch` that did not handle it. This is how you make
"impossible states" unrepresentable in TypeScript.

```ts
type RequestState =
  | { status: 'idle' }
  | { status: 'loading'; startedAt: number }
  | { status: 'success'; data: User; etag: string }
  | { status: 'error'; error: ApiError; retryable: boolean };

function render(s: RequestState): string {
  switch (s.status) {                    // s is narrowed per case
    case 'idle':    return 'nothing yet';
    case 'loading': return `loading since ${s.startedAt}`;   // startedAt exists
    case 'success': return s.data.name;                      // data exists
    case 'error':   return s.error.message;                  // error exists
    default:        return assertNever(s);                   // catches new variants
  }
}

function assertNever(x: never): never {
  throw new Error(`Unhandled variant: ${JSON.stringify(x)}`);
}
```

- **`assertNever(s)` in the `default` is the whole point.** The moment you
  add a `{ status: 'cancelled' }` variant, every `switch` that ends in
  `assertNever` fails to compile until you handle it. Without it, a missing
  case silently returns `undefined` at runtime.
- `never` is also the return type of a function that never returns; passing
  the narrowed leftover to it is the exhaustiveness check. TS's `noImplicitReturns`
  plus this pattern is the complete solution.
- **Discriminant field names**: `status`, `type`, `kind`, `tag` — pick one and
  be consistent. It must be a **literal type**, not `string`, or the union
  does not discriminate:
  ```ts
  type Bad = { status: string; data: User } | { status: string; error: E };
  // no discrimination: s.data does not exist in the error branch
  ```
- Optional discriminant: `{ status?: 'idle' }` **breaks** the discrimination
  (the discriminant must be present in every member). Make it required.
- Discriminated unions are the answer to "optional fields of different shapes"
  — instead of `data?: User; error?: ApiError; loading?: boolean` (which
  permits all three at once and none of them), use the union above (which
  permits exactly the legal combinations).

## Exhaustiveness beyond switch

`assertNever` is the pattern; where it goes varies:

- **Switch statement** (above) — the default case.
- **Array `.map` over variants** — a lookup object keyed by discriminant is
  exhaustive only if typed as a mapped type:
  ```ts
  const labels: Record<RequestState['status'], string> = {
    idle: 'Nothing yet', loading: 'Loading', success: 'Done', error: 'Failed',
  };   // add a variant -> this object is missing a key -> compile error
  ```
  `Record<Union, T>` (or a `satisfies` object) is the "no new key silently
  ignored" version. A bare object literal is not checked for *exhaustiveness*
  unless you annotate it as `Record<...>`.
- **A catch-all `default` that does NOT call `assertNever`** silently swallows
  new variants. That is the trap.

## Never `any`

`any` is contagious: one `any` in a position disables checking for everything
that flows through it, silently. `unknown` is the type-safe "I do not know
yet" — it forces you to narrow before use.

```ts
// Bad: json is any; data.name and data.id are unchecked.
const data = (await res.json());   // any

// Good: unknown, then narrow (see guards below).
const data: unknown = await res.json();
```

- `any` is acceptable in exactly two places: a value you are narrowing in the
  next line (`const x = y as any; if (isFoo(x)) ...` is still a cast), and a
  deliberate escape hatch with a comment. Everywhere else it is a bug.
- Prefer, in order: `unknown` > a precise union > a generic `<T>` constrained
  to the shape > `any`.
- `// @ts-expect-error` (not `@ts-ignore`) — it **errors if the next line is
  actually fine**, so it goes stale loudly when the code is fixed. `@ts-ignore`
  silently suppresses forever. Use it as a temporary marker with an issue
  link, and drive the count to zero.
- `strict: true` in `tsconfig.json` enables `noImplicitAny`,
  `strictNullChecks`, `strictFunctionTypes`, etc. `strictNullChecks` is the one
  that catches the most real bugs (`string | undefined` vs `string`).
- `noUncheckedIndexedAccess: true` makes `arr[0]` be `T | undefined` (arrays
  can be sparse/out of bounds). It is stricter but it models reality and
  catches real off-by-one bugs. Same value for correctness.

## Narrowing and type guards

Narrowing is how `unknown` becomes a real type. Techniques, from narrowest to
broadest:

```ts
// 1. typeof / instanceof / in — built in
if (typeof x === 'string') { x.toUpperCase(); }     // x: string
if (x instanceof Error) { x.message; }
if ('name' in x) { x.name; }

// 2. Discriminant check on a union
if (state.status === 'success') { state.data; }

// 3. User-defined type guard — the workhorse for API/domain types
function isUser(x: unknown): x is User {
  return typeof x === 'object' && x !== null
    && typeof (x as any).id === 'string'
    && typeof (x as any).name === 'string';
}

// 4. Assertion function (throws, narrows) — good for validated boundaries
function assertUser(x: unknown): asserts x is User {
  if (!isUser(x)) throw new ValidationError('not a User');
}

function render(u: unknown) {
  if (isUser(u)) return u.name;    // u: User here
  return 'unknown';
}
```

- A type predicate is `x is User`; the function body must actually check it
  (TypeScript trusts you — a guard that lies is unsound and a bug).
- Prefer **`asserts x is T`** (assertion function) at a parse/validate
  boundary, and **`x is T`** (guard) for branching.
- `in` narrowing works for union members (`'data' in s` narrows to the branch
  with `data`) and, since TS 4.9, for unlisted-property checks on object types.
- Narrowing does not survive assignment to a `let` of a wider type or through
  a closure that runs later, in some cases; when narrowing "does not work",
  the fix is usually to assign to a `const` in the narrowed branch.
- For `unknown` reaching a function expecting a type, do **not** cast — make
  the function accept `unknown` and narrow inside, or narrow at the call site
  with a guard.

## `satisfies` vs `as`

- **`as` is a claim / assertion / cast.** It tells the compiler "trust me",
  produces no error if you are wrong, and is unchecked at runtime. Use it for
  a genuine downcast (an `unknown` you have *just* narrowed, a widening for a
  legacy API), never to silence an error you have not understood.
- **`satisfies` (4.9+) checks a value against a type without changing the
  value's inferred type.** This is the fix for the classic "I annotated and
  lost my literal types" problem.

```ts
const routes = {
  home:   { method: 'GET',  auth: false },
  create: { method: 'POST', auth: true },
};

// `as const` widens nothing but freezes; `satisfies` checks without freezing:
const routes = {
  home:   { method: 'GET',  auth: false },
  create: { method: 'POST', auth: true },
} satisfies Record<string, { method: 'GET'|'POST'; auth: boolean }>;
// routes.home.method is still the literal 'GET' (not 'GET'|'POST')
// and a typo (`method: 'DELTE'`) is a compile error.

const theme = { primary: '#0af' } satisfies Record<string, string>;
theme.primary        // still '#0af' (literal), not string
```

- `as const` makes a value deeply readonly and literal. `satisfies` validates
  shape and keeps inference. Use `as const` for arrays/objects you want
  frozen and literal; use `satisfies` for config objects you want *checked*
  against a type but still inferred precisely. They compose:
  `{...} as const satisfies Config`.
- `as const` on a wide object can make it painful (everything readonly and
  literal); `satisfies` is the gentler, more common tool in app code.

## Generics and constraints

- Constrain a generic to the operations you use. `<T extends { id: string }>`
  means the body may call `.id` and the compiler enforces it for every
  instantiation.
- **`const` type parameters (5.0+)** make a generic infer a literal:
  ```ts
  function get<K extends PropertyKey>(obj: Record<K, unknown>, key: K): Record<K, unknown>[K] { return obj[key]; }
  ```
  With `const`, passing `'a' as const` or a literal infers the literal type, not
  `string`. This replaces many `as const` hacks.
- `NoInfer<T>` (5.4+) blocks inference at that parameter so a default
  controls it — useful for `function f<T>(v: T, fallback: NoInfer<T>)`.
- `extends` on a generic constrains **what can be passed**, not the shape of
  the output. If you need the output to keep the specific subtype, return `T`,
  not `SomeBase<T>`.

## The runtime-vs-type boundary (the non-negotiable rule)

**Types are erased. `JSON.parse` returns `any`; a cast does not check
anything.** The moment data crosses from outside your program — a fetch
response, a query param, a file, `localStorage`, a message, a WebSocket frame
— it is `unknown` and must be **validated at runtime**. A type annotation on
untrusted data is a comment that lies.

```ts
// The lie: this is a claim, not a fact.
const user = (await fetch('/api/user').then(r => r.json())) as User;

// The truth: validate, then the type is earned.
const body: unknown = await fetch('/api/user').then(r => r.json());
if (!isUser(body)) throw new ValidationError('malformed user');
```

Validate at the **edge** and let validated types flow inward:

- Pick one validator: **Zod** (schema doubles as a type via `z.infer`), or
  **Valibot**, or hand-written type guards for small shapes. Schema-first
  (Zod) means the runtime check and the static type cannot drift.
- Validate every network response, every env var, every parsed cookie/JWT
  payload, every `localStorage` read, and every message off a queue or
  WebSocket. Assume every one of these can be wrong or hostile.
- Set `compilerOptions.strict` **and** keep the boundary schema. The type
  system stops you misusing your own data; only validation stops you being fed
  wrong data.

```ts
import { z } from 'zod';

const UserSchema = z.object({
  id: z.string(),
  name: z.string(),
  age: z.number().int().nonnegative(),
});
type User = z.infer<typeof UserSchema>;   // the type comes FROM the schema

const raw: unknown = await res.json();
const user = UserSchema.parse(raw);        // throws ZodError with a path if bad
```

See `python-data-modeling` for the same boundary discipline in Python, and
`rest-api-implementation` for the response validation side.

## Gotchas

- **Excess property checking** only applies to a **fresh object literal**
  assigned to a typed target. `{ a: 1, b: 2 }` assigned to `{ a: number }`
  errors, but a variable (or a spread) does not get the excess-property check.
  It is why "it worked before I refactored" for object literals.
- **`as const` is not validation.** It only tells the compiler; it does not
  check anything at runtime and it does not deep-freeze in JS.
- **`in` narrowing and `Object.hasOwn`** differ for inherited properties; use
  `Object.hasOwn(o, 'k')` (ES2022) when you care.
- **Optional properties and `exactOptionalPropertyTypes`**: with it on,
  `{ a?: string }` does **not** accept `{ a: undefined }` explicitly; without
  it, it does. Turning it on is stricter and catches real bugs but breaks
  code that passes `prop: undefined` — decide per project, document it.
- **Readonly arrays**: `readonly T[]` will not accept a `T[]` in every
  position, and `readonly` is shallow. `as const` gives `readonly` deeply but
  freezes literals you may want to pass as mutable.
- **Union of object types is a poor substitute for a class with methods** — the
  discriminant gives you data safety, not behaviour. Keep logic in functions
  that switch on the discriminant, not methods on the variants.
- **`unknown` in a generic position without a constraint** propagates `unknown`
  everywhere and is as unusable as `any`. Constrain it (`<T extends object>`)
  or narrow it.
- **Do not use type predicates to lie.** `function isFoo(x: unknown): x is Foo
  { return true; }` compiles and is a bug waiting to happen.

## House defaults

- `strict: true` plus `noUncheckedIndexedAccess` and
  `exactOptionalPropertyTypes` in `tsconfig.json`.
- Discriminated unions for every state machine / event / API response;
  `assertNever` in every exhaustive switch.
- `unknown` at every boundary, narrowed with guards; zero `any` in app code;
  `@ts-expect-error` only as a temporary, tracked marker.
- `satisfies` to check config shapes without widening; `as` only for
  deliberate, commented downcasts.
- One schema validator (Zod by default) at every untrusted edge; the static
  type is derived from the schema, never asserted onto raw data.
