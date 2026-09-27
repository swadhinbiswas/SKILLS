---
name: service-boundaries-and-monoliths
description: Decide what to split into a service and what to leave in one deployable - cohesion, the monolith to modular-monolith to microservices spectrum, the real cost of distribution, extracting a module without a rewrite, and keeping boundaries intact in the codebase. Use when the user says "should we split this service", "monolith vs microservices", "modular monolith", "shared database", "service boundaries", "extract this module", "our services are too coupled", or is reviewing whether a codebase should be decomposed.
compatibility: Language-agnostic. Mentions language-specific module tools as examples; verify before relying on them.
metadata:
  version: "1.0"
---

# Service Boundaries and Monoliths

The default answer is **one deployable**. Splitting is a decision with a
permanent maintenance cost, and it is almost never justified by "the codebase
is getting big". It is justified by a difference in scaling profile, data
ownership, or release cadence that you can name out loud.

## The spectrum

These are not three rungs of maturity. They are three different cost profiles,
and picking the wrong one is expensive in both directions.

| | Monolith | Modular monolith | Microservices |
|---|---|---|---|
| Deployable units | 1 | 1 | N |
| Cross-cutting change | easy | easy, if the module API is respected | N services to version, deploy, and coordinate |
| Local dev | one process | one process | every service, or a compose file, or mocks |
| Transaction across modules | real transaction | real transaction | no; you need saga/outbox/consistency |
| Scaling | uniform | uniform | per-service, per-component |
| Failure mode | one bug, whole app | one bug, whole app | partial degradation (if designed) |
| Ops cost | low | low + discipline | high: pipelines, meshes, on-call, version skew |
| Team fit | 1–5 engineers | 5–20, or several teams on shared code | Multiple teams, genuinely independent release needs |

**The honest framing:** a modular monolith is a monolith with rules. The rules
are the whole value: a module boundary that no one imports across is real
enforcement; a boundary that is a folder name is a comment.

## Step 1 — Do you actually need a split?

Answer all four yes before you split:

1. **Different scaling profile.** The image service needs 100× the CPU of the
   billing service. Co-scaling them means 100× your infrastructure cost.
2. **Different data ownership / compliance.** Card data must live in a system
   with an audited boundary. That is a legal requirement, not a style choice.
3. **Different release cadence.** Team A ships daily, team B ships quarterly,
   and the shared monolith makes A wait for B. This is the strongest technical
   reason and the most commonly under-estimated.
4. **A real fault-isolation need.** One module's bad deploy must not take the
   whole product down, and you have evidence (incidents) that this matters.

If the answer is "it is big", "it is slow to build in", or "we want to use
Kafka", the answer is **no**. Big codebases are a modularity and ownership
problem, and splitting into network services makes code organisation *harder*,
not easier: you can no longer rename a function without a coordinated deploy.

## Step 2 — Find the boundary by cohesion, not by table

A service boundary is a **transactional boundary**: a set of data that must
change together. Find it by asking what must be atomic.

- `orders` + `order_items` + `order_status_history`: one thing. They are
  written in one transaction. They belong together.
- `orders` + `inventory`: usually separate. Reserving stock inside the order
  transaction is a choice; undoing it is a saga.
- `orders` + `recommendations`: separate. Different scaling, different data,
  and a failure in recommendations should not fail checkout.

The heuristic: **if you cannot name the transaction boundary, you have not
found the service boundary yet.** Splitting at a table boundary and then
needing cross-service transactions is how teams end up with distributed
monoliths — N services that must deploy together.

Signals you picked the wrong boundary:

- Any endpoint that must call 4+ other services. That is not a service, it is
  a function wearing a network.
- Any change that requires all services deployed in lockstep. Distributed
  monolith; you have paid all the costs and none of the benefits.
- A shared database with all services reading and writing all tables. Now the
  "services" are just HTTP wrappers and the schema is the coupling.

## Step 3 — What distribution actually costs

Be explicit about this when you advise. It is the part that a
"let's go microservices" plan leaves out.

- **A local function call becomes a failure mode.** Every call can time out,
  be refused, return a partial result, or be duplicated. You inherit
  retry/backoff/circuit-breaking (see
  `skills/architecture/resilience-patterns/SKILL.md`), timeouts, idempotency,
  and distributed tracing — for code that used to just work.
- **Transactions become eventual consistency.** No rollback across services.
  You need an outbox (see
  `skills/api-design/webhooks-and-integrations/references/outbox.md`), a saga,
  and a compensation story for every multi-service write.
- **Debugging gets harder.** A user-visible failure is now a trace across
  N services. You need correlation ids, structured logs, and a tracing
  backend before you split, not after.
- **Deploys multiply.** N pipelines, N rollback stories, N versions of a shared
  client library, and version skew (service A calling B's new API while C is
  on the old one).
- **Local development degrades.** `docker compose up` for 8 services is a
  different developer experience from `npm run dev`.
- **Operational surface explodes.** You now own service discovery, load
  balancing, mesh or not, dashboards per service, on-call per service.

The honest framing for a team that asks: "you are trading a compile error for
a 3am production incident, and you need to be sure the trade is worth it."

## Step 4 — Extract a module without a rewrite

Never do a "big bang" split. The sequence that works is strangler-fig, and
each step is independently shippable.

1. **Define the module's public surface first.** A single interface or module
   boundary that the rest of the codebase calls. Nothing imports its internals.
   In Go this is a package with a narrow exported API; in Java/Kotlin, a
   module with enforced boundaries (Maven modules, Gradle project
   dependencies); in Python, a package with `__all__` and a lint rule; in
   TypeScript, a package boundary. Verify the tool's current flag with
   `--help`/docs — the mechanisms differ a lot.
2. **Move the code, keep the process.** Physically relocate the module; keep
   it in the same deployable. Now you have a boundary with zero distribution
   cost, and you can find out what actually crosses it.
3. **Enforce the boundary with a test or a linter.** A CI check that fails on
   an import across the boundary is what makes this real. Without
   enforcement it regresses within two sprints, always.
4. **Give it its own data.** Even in the same database, a module owning its
   tables (no other module writes them) is most of the win. Enforce with
   grants: `REVOKE INSERT, UPDATE, DELETE ON orders FROM app_user`, and let
   only the module's own code have those privileges.
5. **Route at the edge.** Add a facade in front of the current entry point so
   calls to the module go through one place. This is the seam you will cut
   when you extract the process.
6. **Extract the process.** Deploy the module as its own service. The facade
   now calls it over the network; the interface is unchanged, so nothing else
   had to change.
7. **Take the data with it.** Move the tables to that service's own database.
   Until this step the "service" still shares a database and is not a real
   boundary. Do this last, after the process boundary has stabilised, because
   splitting a schema and a code boundary at the same time is where projects
   die.

Stop at any step and you have something better than you had. Steps 1–4 (a
modular monolith) are where most of the value is, and they are cheap.

## Step 5 — Keep the boundary in the codebase

Boundaries rot from the inside out. What stops them:

- **One way in.** No cross-module direct reads of another module's tables, even
  in the same database. If module B needs A's data, call A's interface.
- **No shared "common" module that everything imports.** It is the first
  boundary to die and it takes others with it. Duplication across two modules
  is cheaper than a shared one used by five.
- **Events for fan-out, calls for queries.** A module that needs to react to
  another module's change subscribes to its event; it does not poll its tables.
- **Enforce it in CI**, not in review. An architecture test or linter rule that
  fails the build is the only thing that survives.
- **Watch the metric that predicts rot:** the number of cross-boundary
  imports, over time. It should be flat or falling. If it is rising, the
  boundary is nominal.

For a distributed system, the equivalents: no shared database (each service
owns its data), versioned APIs with a deprecation policy, and a schema/contract
check in CI. Contract testing between consumers and providers catches the
version-skew break before deploy.

## Gotchas

- **A shared database between services is the most common reason microservices
  fail.** The moment two services write the same table, they are one service
  with HTTP in the middle, and you pay all the distributed costs with none of
  the independence.
- **Nanoseconds become milliseconds.** An in-process call is ~100ns; a
  cross-container call is ~1ms and a cross-region call is tens of ms. A hot
  loop of 50 internal calls becomes 50ms. Count the calls in the request path
  before you accept the design.
- **Distributed monolith**: N services that must be deployed together. If the
  lockstep deploy is the norm after a few months, the split was wrong; merge
  back rather than pretending.
- **Splitting by technical layer** (a "controllers" service, a "services"
  service) instead of by domain is the classic wrong cut. Split by what
  changes together and what must be transactional.
- **Chatty services.** If the average request touches 4+ services, you have
  not reduced coupling, you have decorated it. Consolidate or add a
  purpose-built BFF for a specific client.
- **A service per team is a real cost.** On-call, pipelines, dashboards, and
  version compatibility do not halve when you split; they multiply.
- **The database is the boundary that matters.** Code boundaries are
  convention; a permission that prevents service A from writing service B's
  table is law.
- **Migrating a monolith to services is a multi-quarter project.** Anyone
  presenting it as a sprint is selling something.
- **Over-modularising a monolith is its own failure**: 40 modules, each with
  an interface, each used by one caller, and now a change requires reading
  five files. Coarse is fine. Two or three well-chosen modules beat twenty
  small ones.

## When to split anyway (a real trigger list)

- One component's resource profile is so different that co-scaling is wasteful
  (image/video transcoding, ML inference, high-volume ingestion).
- A regulatory boundary requires auditable data isolation (PCI, health data,
  regional data residency).
- Teams on incompatible release cadences, with a measured cost from waiting.
- A specific component has taken down the whole product more than once.
- The organisation has genuinely independent teams and the operational
  maturity (on-call, tracing, pipelines) to support it.

If none of these is true, spend the time on module boundaries inside the
monolith, on the database schema, and on the query performance. It is the
higher-return work and it is what actually blocks delivery.

## Output template

When advising on a split, produce:

```markdown
## Recommendation
[Monolith / modular monolith / split X out] — and the requirement that drives it

## Boundary justification
Transactional boundary for X: <what must be atomic>
Scaling profile of X: <n vs the rest>
Release cadence of X: <vs the rest>

## Costs accepted
<failure modes, eventual consistency, deploy/ops surface, on-call>

## Extraction plan
Steps 1-7, each independently shippable, with the rollback for each

## Enforcement
CI rules, table grants, the metric that tracks rot
```

## Safety notes

- Do not recommend a split to a team that has not asked for one, and do not
  recommend one to a team that cannot staff on-call for the new service.
- When reviewing an existing architecture, name the specific coupling (shared
  table, synchronous call chain, lockstep deploy) for each recommendation.
  "Consider microservices" is not a review finding.
- Extraction steps 1–4 are safe and reversible. Step 6 and 7 (process and
  data) are the ones that need a rollback plan written down in advance.
