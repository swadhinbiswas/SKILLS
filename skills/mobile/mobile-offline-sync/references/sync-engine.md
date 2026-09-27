# Sync engine reference

Load this when implementing the pull/push loop, choosing a conflict strategy, or
debugging a sync that loses or duplicates data.

## Outbox schema (worked)

Same database as the app data; the outbox is just a marked set of rows plus a
push receipt table.

```sql
-- App data with sync metadata
CREATE TABLE notes (
  id          TEXT PRIMARY KEY,
  body        TEXT NOT NULL,
  title       TEXT NOT NULL,
  deleted     INTEGER NOT NULL DEFAULT 0,
  rev         TEXT,                -- server revision this row is based on
  sync_state  TEXT NOT NULL DEFAULT 'clean',  -- 'clean' | 'pending' | 'inflight'
  client_seq  INTEGER NOT NULL     -- monotonic local counter (HLC-ish tiebreak)
);

-- Push receipt: what we sent, so we can detect our own retries
CREATE TABLE outbox (
  id           TEXT PRIMARY KEY,   -- = notes.id (one pending change per row)
  op           TEXT NOT NULL,      -- 'upsert' | 'delete'
  payload      TEXT NOT NULL,      -- JSON snapshot of the row to send
  base_rev     TEXT,               -- the rev this edit was based on (conflict detect)
  attempts     INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_outbox_ready ON outbox(next_attempt_at);

-- Sync cursor
CREATE TABLE sync_meta (k TEXT PRIMARY KEY, v TEXT);  -- 'last_pulled_cursor', 'device_id', ...
```

Key invariants:

- **One pending outbox row per entity.** A second local edit updates the
  existing outbox row's payload (coalescing) instead of queueing another. This
  bounds the queue and makes retries idempotent per entity.
- **`base_rev` is captured when the edit is made** (from the row's current
  `rev`), not at push time. That's the "which version did the user edit?"
  question the server needs to detect a concurrent change.
- **`client_seq`** is a local monotonic counter, not wall time — immune to the
  device clock changing. Use it to order local edits when coalescing.

## Push: batched, idempotent, per-row results

```ts
async function pushOutbox(db, api) {
  const rows = await db.getAllAsync(
    `SELECT * FROM outbox WHERE next_attempt_at <= ? ORDER BY rowid LIMIT 100`, nowMs());
  if (!rows.length) return;

  const batch = rows.map((r) => ({
    idempotencyKey: `${deviceId}:${r.id}`,  // stable across retries
    id: r.id, op: r.op, baseRev: r.base_rev, data: JSON.parse(r.payload),
  }));

  let res;
  try {
    res = await api.post('/sync/push', { changes: batch }, {
      headers: { 'Idempotency-Key': hash(batch.map(b => b.idempotencyKey).join(',')) },
    });
  } catch (netErr) {
    // Network/server failure: keep rows queued, back off. Do NOT clear.
    await backoff(rows);
    return;
  }

  for (const outcome of res.results) {   // per-row results
    if (outcome.status === 'applied') {
      await db.withTransactionAsync(async () => {
        await db.runAsync(`UPDATE notes SET rev=?, sync_state='clean' WHERE id=?`,
          outcome.serverRev, outcome.id);
        await db.runAsync(`DELETE FROM outbox WHERE id=?`, outcome.id);
      });
    } else if (outcome.status === 'conflict') {
      await resolveConflict(db, outcome);          // per strategy
    } else {
      await db.runAsync(`UPDATE outbox SET attempts=attempts+1, next_attempt_at=? WHERE id=?`,
        nextBackoff(outcome), outcome.id);
    }
  }
}
```

- **Idempotency**: the `idempotencyKey` is stable per (device, row). A retry of
  the same batch after a timeout returns the original result instead of
  re-applying. This is what makes "did the request actually arrive?" safe.
- **Never clear the outbox optimistically.** Clear only rows the server
  confirms as `applied`. A row not mentioned in the response stays queued.
- **Coalescing** in `outbox` bounds memory and means a user who edits one note
  50 times offline pushes one row.

## Pull: cursor-based, atomic advance

```ts
async function pullChanges(db, api) {
  let cursor = await db.getFirstAsync(`SELECT v FROM sync_meta WHERE k='last_pulled_cursor'`);
  cursor = cursor?.v ?? null;
  const page = await api.get('/sync/changes', { since: cursor, limit: 500 });
  // page = { changes: [...], nextCursor, complete }

  await db.withTransactionAsync(async () => {
    for (const ch of page.changes) {
      if (ch.op === 'delete') {
        await db.runAsync(
          `INSERT INTO notes(id,deleted,sync_state) VALUES(?,1,'clean')
             ON CONFLICT(id) DO UPDATE SET deleted=1, sync_state='clean'`, ch.id);
      } else {
        // Do NOT mark pending: a pulled row is clean, or the sync loops forever.
        await upsertCleanRow(db, ch);  // rev = ch.rev, sync_state='clean'
      }
    }
    // Advance the cursor only after the WHOLE page applied.
    await db.runAsync(
      `INSERT INTO sync_meta(k,v) VALUES('last_pulled_cursor',?)
         ON CONFLICT(k) DO UPDATE SET v=excluded.v`, page.nextCursor);
  });
}
```

- **Page atomically + advance the cursor in the same transaction** = no torn
  views, no lost deltas.
- **Pulled rows are `clean`.** If pull set them pending, the next push would
  re-send server data back to the server — the classic write-storm loop.
- **Field-level merge on pull**: if the local row is `pending` (has unsynced
  local edits) and the server row changed too, apply the field-level merge
  instead of overwriting (see below).
- **Long absence**: if the server says the cursor is too old (`410 Gone`), wipe
  local and do a full resync (pull all), not page through a giant log.

## Conflict resolution recipes

### Last-write-wins (only with a monotonic clock)

Use a **hybrid logical clock**, not `Date.now()`:

```ts
// HLC: (wallMs, counter, nodeId). Advances monotonically per device and
// respects causal order even when device clocks disagree.
function tick(prev, wallNow) {
  const l = Math.max(prev.wallMs, wallNow);
  const c = l === prev.wallMs ? prev.counter + 1 : 0;
  return { wallMs: l, counter: c, node: deviceId };
}
function compare(a, b) {
  return a.wallMs - b.wallMs || a.counter - b.counter || (a.node < b.node ? -1 : 1);
}
```

- Resolve: higher HLC wins. Ties broken by `node` for determinism.
- Still loses concurrent edits (last writer wins, the other is gone) — fine for
  low-stakes, single-writer-per-field data.

### Field-level merge (best UX for shared rows)

Track a per-field timestamp (or per-field rev):

```ts
type Row = { id: string; rev: string; fields: Record<string, { value: any; hlc: HLC }> };
// On pull of a changed row, merge per field: for each field, keep the version
// with the higher hlc. Local pending edits keep their field (they're newer).
// Result: two devices editing different fields both survive; editing the SAME
// field is still LWW.
```

### Server-authoritative (prices, permissions)

Server wins that field always; if the local edit was on such a field, surface
"this changed on the server" rather than silently reverting. Keep the user's
intent where you can (re-apply with a "retry as new" affordance).

### Delete vs edit

Decide once, apply consistently:
- **Delete wins**: keep the tombstone; an edit pushed after a delete is
  rejected and the row stays deleted. Prune the tombstone only after the
  server confirms both the delete and that no pending edit references it.
- **Edit resurrects** (rare): only if the edit's `base_rev` predates the
  delete *and* the server allows it.

## Background sync, concretely

```ts
// Foreground triggers (do these; they're not OS-throttled)
AppState.addEventListener('change', (s) => { if (s === 'active') syncNow(); });
NetInfo.addEventListener(state => { if (state.isConnected) syncNow(); });
await syncNow();  // on app start

// True background (best-effort top-up; OS decides if/when it runs at all)
// expo-background-task, registered at app start (top-level, not in a component):
TaskManager.defineTask('SYNC', async () => {
  try { await syncNow(); return BackgroundTaskResult.Success; }
  catch { return BackgroundTaskResult.Failed; }   // let the OS retry later
});
await BackgroundTask.registerTaskAsync('SYNC', { minimumInterval: 15 /* min */ });
```

- **Register the task at module top level**, not inside a component effect —
  it must be defined before the app backgrounds or registration is lost.
- **iOS**: `BGAppRefreshTask`; the system decides when (often late or never).
  **Android**: `WorkManager` with a ~15min minimum, deferred by Doze.
- **Neither is a guarantee.** Anything that must run regardless of the app
  being open is a **server-side** job.

## Debugging sync bugs

| Symptom | Likely cause | Check |
|---|---|---|
| Data comes back after I deleted it | Hard delete locally; server still has it; no tombstone | Is `deleted` a tombstone that syncs? |
| Edits keep getting overwritten | LWW by raw client `Date.now()` with skewed clocks | Switch to HLC or rev-based; log both clocks |
| Duplicate records after a retry | Non-idempotent create; no client id / idempotency key | Replay the same batch; expect identical result |
| Infinite sync loop / battery drain | Pull marks rows pending, or sync triggers on its own writes | Are pulled rows `clean`? Re-entrancy guard? |
| Changes stranded after a force-kill | Local write and outbox insert not in one transaction; no `dirty`/`pending` flag | Kill mid-sync; are changes still queued? |
| Stuck on "3 pending" that never clears | Push response not parsed; per-row results not handled; backoff time not honoured | Log the raw push response per row |
| A long absence loses data / 410 | Cursor expired; needs full resync | Implement the full-resync fallback |
| Half-pulled view after a failure | Cursor advanced before the page fully applied | Cursor advance in the same transaction as the apply |
| Auth 401 during sync | Token expired mid-sync; queue dropped | Refresh token; pause outbox until valid |
