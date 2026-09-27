---
name: mobile-offline-sync
description: Design offline-first mobile apps that survive real networks - a local SQLite database as the source of truth, a sync engine with durable change queues, conflict resolution, and background sync. Covers the pitfalls that break sync in the field: clock skew, partial sync failures, non-idempotent writes, and unbounded queues. Use when a mobile app must work without a connection, when a user complains "it didn't save", when data diverges between devices, or when adding a sync layer. Triggers on "offline first", "sync", "conflict resolution", "CRDT", "outbox", "expo-sqlite", "network error lost data", "background sync", "last-write-wins", "duplicate records after retry", "cached data".
compatibility: SQLite via `expo-sqlite` or a native SQLite binding; background execution via expo-background-task or a background fetch API. Approaches are engine-agnostic.
metadata:
  version: "1.0"
---

# Mobile Offline Sync

The rule that makes offline work: **the local database is the source of truth;
the server is a replica that happens to be shared.** The UI reads and writes
only local data. Sync is a background process that reconciles the two. If the UI
ever waits on the network to show data, it is not offline-first.

## Architecture

```
   UI / hooks
      ↓  (always local, instant)
   Local store (SQLite, or MMKV for trivial KV)
      ↓
   Outbox (durable queue of local mutations, in the same DB, same transaction)
      ↓  sync engine (on app start, on focus, on connectivity, on a timer/background task)
   Server API  ←→  conflict resolution
```

Two rules that make this correct:

1. **Every local write and its outbox entry happen in one transaction.** If the
   app is killed between "wrote to SQLite" and "queued the sync", the change is
   stranded forever. Put them in the same transaction or the same write.
2. **Every server write is idempotent.** Sync retries. A retried
   "create order" must not create two orders. Send a client-generated `id` (or
   an idempotency key) and have the server upsert/dedupe on it.

## Local database

- **`expo-sqlite`** (Expo) or a native SQLite binding (bare: `react-native-sqlite-storage`,
  `op-sqlite`/`quick-sqlite` for speed) for anything structured, queryable, or
  that will grow. Store the rows the UI renders.
- **MMKV** (`react-native-mmkv`) for small, hot, synchronous values: auth token,
  flags, a last-seen timestamp. Not a database; not queryable.
- **AsyncStorage** for trivial async key/value only. Not for a dataset, not on
  a hot read path (async, and reads block a first render).

The local schema mirrors the server but adds sync metadata per row:

```sql
CREATE TABLE notes (
  id          TEXT PRIMARY KEY,          -- client-generated (uuid/nanoid); stable across retries
  body        TEXT NOT NULL,
  updated_at  INTEGER NOT NULL,          -- client wall clock (see clock skew below)
  server_rev  INTEGER,                   -- the server revision this row is based on (conflict detection)
  deleted     INTEGER NOT NULL DEFAULT 0,-- tombstone: deletes must sync too
  dirty       INTEGER NOT NULL DEFAULT 0 -- 1 = local change not yet confirmed by server
);
CREATE INDEX idx_notes_dirty ON notes(dirty) WHERE dirty = 1;
```

- **Client-generated IDs** (not server autoincrement) so a row can be created
  offline and referenced before the server has seen it.
- **`dirty`** flag: cheap change detection and lets the sync engine select only
  unsynced rows.
- **Tombstones** (`deleted`): a delete is a change like any other. If you hard
  delete locally, the server never learns and the row comes back on next sync.
  Keep tombstones until the server confirms the delete.
- **`server_rev`** (or a version/etag the server returns) is what makes conflict
  detection possible: when pushing, tell the server which revision you're
  editing, and it rejects if it moved.

## The sync engine

A pull-then-push loop over a paginated delta feed:

```
on app start / on focus / on connectivity regained / on a background timer:
  1. PULL  GET /changes?since=<last_pulled_cursor>
           → apply server changes to local DB (last-write-wins per field, or
             server is authoritative for pulled rows)
  2. PUSH  POST /batch  with the local outbox (dirty rows), idempotently
           → on success: clear dirty, set server_rev, advance cursor
           → on conflict (409): run conflict resolution
           → on network/server error: LEAVE rows dirty, retry with backoff
  3. Advance last_pulled_cursor ONLY after a fully successful pull.
```

- **Cursor-based delta, not timestamps.** `?since=<opaque cursor>` where the
  server returns a monotonic cursor for its change log. Timestamps as cursors
  miss same-millisecond writes and depend on clock agreement — the classic
  sync bug.
- **Push a batch** with a stable per-row id; the server upserts and returns
  per-row results (applied / conflict / rejected) so one bad row doesn't fail
  the batch.
- **Per-row results** matter: a batch of 50 with one conflict should apply 49
  and resolve 1, not retry all 50 forever.
- **Backoff with jitter** on failures; **don't busy-retry** on a server outage
  (battery + rate limits).
- **Re-entrancy guard**: don't start a sync while one is running; coalesce
  requests instead.
- **Bounded batch** (e.g. 100 rows) so a big backlog syncs incrementally and
  memory stays flat.

## Conflict resolution

Pick per-field or per-entity based on what the data is.

| Strategy | Use when | Cost |
|---|---|---|
| **Last-write-wins (LWW)** | Independent, low-stakes fields (a note's body, a toggle) | Can silently drop a concurrent edit; relies on trustworthy clocks (see skew) |
| **Server-wins** | Server is authoritative (prices, permissions, moderation) | Local edits to that field are lost; show "changed on another device" |
| **Client-wins (force)** | Rare; a deliberate local override | Can clobber newer server data |
| **Field-level merge** | Two people edited *different fields* of the same row | More bookkeeping; best UX for shared docs |
| **Version vector / CRDT (lite)** | Rich collaborative editing, offline-first merges | Real complexity — only adopt deliberately (e.g. `yjs`, Automerge); needs a real CRDT library, not a hand-rolled one |
| **Manual (conflict prompt)** | Rare, high-stakes, low-frequency conflicts | Needs UI; avoid for high-churn data |

- **LWW needs a monotonic, comparable clock.** Client `Date.now()` is
  unreliable (see skew). If you do LWW, prefer a **server-assigned revision or
  HLC (hybrid logical clock)** over raw wall time. An HLC (`wall_time,
  counter, node_id`) keeps causality under clock skew.
- **Detect the conflict, don't just overwrite.** Compare the `server_rev` the
  edit was based on against the current server rev. If they differ, someone else
  changed it → run resolution. If they match, it's a clean push.
- **Field-level merge** is the pragmatic sweet spot for most apps: keep each
  field with its own `updated_at`/rev, and merge per field, so two offline
  devices editing different fields of the same record both survive.
- **Deletes vs edits**: if one device deletes a row and another edits it, decide
  and be consistent (e.g. delete wins, or "item was deleted" is surfaced). Store
  the tombstone so you can reason about it.

## Background sync

- **Foreground (always do this first)**: sync on app start, on returning to
  foreground (`AppState` → `active`), and on connectivity regained
  (`expo-network` / NetInfo). This covers the majority of usage and is not
  subject to OS background limits.
- **True background** (when the app is closed) is **OS-throttled and not
  guaranteed** on both platforms. Use it as a best-effort top-up:
  - **iOS**: `BGAppRefreshTask` via `expo-background-task`; iOS decides when
    (often not at all). Background *fetch* is opportunistic.
  - **Android**: `WorkManager` (min ~15min periodic) via
    `expo-background-task`; Doze/idle defers it.
  - **Do not promise background sync.** Anything that must happen "even if the
    app is closed for a week" belongs on a **server-side** job (e.g. push
  notifications, scheduled emails), not in the client.
- **Sync on a meaningful trigger, not a tight timer** — foreground/focus/
  connectivity — to save battery and avoid OS throttling.

## The real pitfalls

1. **Clock skew.** `updated_at` from `Date.now()` is not monotonic across
   devices: the user's phone clock can be minutes/hours off, timezone/DST
   confuses humans, and a client clock change rewrites history. Symptoms: "my
   edit keeps getting overwritten", "the newest change always loses".
   **Fix**: use a server cursor/rev for truth, an HLC for local ordering, or
   LWW-by-server-arrival. Never resolve a conflict by comparing raw client
   timestamps alone.
2. **Partial sync failures.** A pull that fetches page 1 of 5 and dies leaves
   a torn view. **Fix**: advance the cursor only after a *complete* pull, and
   apply pages atomically (or mark pulled-but-incomplete and re-pull). A push
   where some rows fail must keep only the failed rows in the outbox.
3. **Non-idempotent writes.** Retries duplicate. **Fix**: client-generated ids +
   server upsert, or an `Idempotency-Key` header per batch; the server
   deduplicates. Test by replaying a batch twice.
4. **Stranded changes.** Killed between local write and outbox insert. **Fix**:
   same transaction; treat the `dirty` flag as the source of truth (a crash
   leaves the row dirty, so the next sync picks it up — the flag, not a
   separate push, is what makes it durable).
5. **Unbounded outbox / backpressure.** A week offline with a chatty user
   creates a huge queue; a single giant push times out and loops. **Fix**:
   bounded batches, coalesce multiple edits to the same row (keep the latest),
   surface "N changes pending" in the UI, and prioritise (new content before
   old).
6. **Sync loops / write storms.** A pull that marks rows dirty (or an
   effect that syncs on every local change) re-pushes what the server just sent
   → infinite loop and battery drain. **Fix**: pulled rows update the local
   row *without* setting `dirty`; guard the sync trigger.
7. **Auth expiry mid-sync.** A 401 leaves the outbox half-pushed. **Fix**:
   refresh the token before/within sync, and on 401 pause the outbox until the
   session is valid (don't drop the queue).
8. **Data loss on "logout" or reinstall.** **Fix**: clear (or encrypt-wipe) the
   local DB on logout; never trust a reinstall to preserve state. If the user
   is logged in on two devices, each pulls the other's changes via the server
   (that's what the change log is for).
9. **Storage growth.** Outbox and tombstones grow forever. **Fix**: prune
   tombstones and synced rows older than the retention window after the server
   has confirmed; cap the outbox by coalescing.
10. **Pulling a huge delta after a long absence.** **Fix**: if the cursor is too
    old for the delta, fall back to a full resync (wipe-and-refetch local from
    the server) rather than paging through a giant log.

## Testing sync

- **Simulate**: a fake server with latency, 500s, 409s, and a kill-switch;
  toggle airplane mode; force-kill the app mid-sync and relaunch (this is the
  stranded-change test).
- **Property/edge cases**: two devices edit the same row offline; delete vs
  edit; pull fails mid-pagination; push returns partial results; a batch is
  replayed (idempotency); clock skewed a day forward; outbox > batch size.
- **Assert the invariants**: no lost acknowledged write; no duplicate rows
  after retries; the local DB is never in a half-pulled state; the UI never
  blocks on the network.

## When you don't need full offline-first

- If the app is read-mostly, an **HTTP cache** (`expo-network` connectivity +
  a stale-while-revalidate query cache persisted to MMKV) is far simpler and
  covers "show me something when offline".
- Full outbox/conflict machinery is worth it for **user-generated data that
  must not be lost** (notes, drafts, forms, messages, edits). Match the design
  to the stakes.

## Gotchas

- **Persisting a query cache is not the same as offline-first writes** — it
  serves stale reads but doesn't queue writes. If edits must survive offline,
  you need the local DB + outbox.
- **`expo-sqlite` is async**; a first-render read of a large table delays the
  first paint. Gate the UI on a quick "is hydrated" flag (e.g. a small MMKV
  marker) and load the big table after mount.
- **SQLite WAL mode** (write-ahead logging) improves concurrent read/write
  behaviour; `expo-sqlite` enables WAL. Don't disable it casually.
- **Transactions matter**: wrap multi-row applies in `withTransactionAsync`
  (expo-sqlite) so a crash can't tear a sync.
- **Don't store secrets in the sync DB unprotected**; the device may be
  compromised. Encrypt sensitive columns or rely on device encryption +
  short-lived tokens.
- **The server must expose a stable change cursor.** A "give me everything
  since <client timestamp>" endpoint will always eventually miss or duplicate
  changes. Build a server-side change log with monotonic cursors.
- **`deleted` tombstones need the server to confirm before you can safely
  prune them**; pruning early can resurrect a deleted row if a stale device
  re-pushes it.

## Review checklist

- [ ] UI reads only from the local DB; nothing blocks on the network.
- [ ] Local write + outbox entry are in one transaction; `dirty` flag is the
      durable source of truth.
- [ ] Client-generated ids + server idempotency/upsert → retries are safe.
- [ ] Pull is cursor-based; cursor advances only after a complete pull.
- [ ] Push is batched with per-row results; failed rows stay queued.
- [ ] Conflict strategy is chosen per-entity; no raw client-timestamp LWW
      (or an HLC if you must).
- [ ] Deletes use tombstones; pruned only after server confirmation.
- [ ] Sync triggers: start, foreground, connectivity; true background is
      best-effort only.
- [ ] Tombstones/outbox pruned; a long-absence full-resync path exists.
- [ ] Tested against: kill-mid-sync, partial failures, replayed batches, two
      devices, skewed clocks.

## Files

- `references/sync-engine.md` — the pull/push loop in detail, conflict
  resolution recipes, and a worked outbox schema.
