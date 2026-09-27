# Editing: more worked examples

Each example shows the specific principle, the before, the after, and why the
after is better. Use them as a pattern library when rewriting a section.

## 1. Procedure with a hidden prerequisite

**Before**

> First, make sure that the environment is properly configured. The service
> requires a number of environment variables to be set, and it is important
> that these are correct, otherwise it may not start.
> Once the configuration is in place, you can then start the service with the
> following command.

**After**

> The service refuses to start if any of these are unset, with
> `config error: WIDGETS_DB_URL is required` in the first 20 lines. Set them
> before starting:
>
> ```bash
> export WIDGETS_DB_URL="postgres://…"
> export WIDGETS_PORT=8080          # optional, defaults to 8080
> make run
> ```
> Success is `widgets dev server on http://localhost:5173`. If you see
> `address already in use`, set `WIDGETS_PORT` to something else.

**Why:** the before hides *which* variables and what failure to expect; the
after is a sequence the reader can run, with the failure mode attached to the
step that causes it.

## 2. Reference entry: constraints belong with the fact

**Before**

> `timeout_ms` — the timeout.
>
> Note that this value should generally be set to something reasonable, and
> that very large values can lead to resource exhaustion issues. It is also
> possible that some endpoints may ignore this.

**After**

> | Parameter | Type | Default | Valid range | Notes |
> |---|---|---|---|---|
> | `timeout_ms` | integer | `5000` | `100`–`120000` | Per-request total, including retries. `GET /v1/exports` ignores it and uses `export_timeout_ms` (default 300000). Values above 120000 return `400 invalid_parameter`. |

**Why:** "reasonable", "generally", and "it is also possible" tell the reader
nothing they can check. A range and an exception are usable.

## 3. Explaining a design choice without apologising for it

**Before**

> We have chosen to use Postgres for the primary datastore. This was a
> difficult decision, and there were many factors to consider. While a
> NoSQL database might offer better performance for certain workloads, the
> relational model is a better fit for our data, which is highly relational.
> Additionally, our team has more experience with Postgres. It is possible
> that in the future we may want to revisit this decision.

**After**

> We use Postgres 16 as the primary datastore. Our data is relational
> (orders → items → adjustments), we need transactional writes across those
> tables, and the team already operates Postgres. A document store would
> require us to implement the joins and cross-table transactions ourselves.
> Revisit if a single document exceeds ~1 MB or the write rate exceeds 50k/s,
> neither of which is on the roadmap.

**Why:** the before makes four passes at the same point and never states the
requirement that decided it. The after names the constraint, the consequence of
the alternative, and a trigger for revisiting — all in four sentences.

## 4. Error message documentation

**Before**

> Requests that fail validation return a 400 response with an error message
> describing the issue. Various error messages may be returned depending on the
> problem.

**After**

> | `code` | `status` | `param` | Cause | Fix |
> |---|---|---|---|---|
> | `missing_field` | 400 | field name | Required field absent | Send the field |
> | `invalid_currency` | 400 | `currency` | Not an ISO 4217 code, or not one you are enabled for | Use a code from your account settings |
> | `amount_too_large` | 400 | `amount_minor` | > 1,000,000,000 for your plan | Split the payment or upgrade |
>
> Every 400 includes `error.param`, the dotted path to the offending field
> (`items[1].sku`). Match on `error.code`, never on `error.message` — messages
> are for humans and change without notice.

**Why:** a consumer integrating against "various error messages" will match on
strings and break. The after gives them stable codes to switch on.

## 5. Comparison with a decision boundary

**Before**

> The new index is faster than the old approach in most cases, though the
> results can vary depending on the data. It's generally recommended to use it,
> although in some situations the old approach might be better.

**After**

> With the new index, a 50k-row lookup is 12 ms (was 90 ms) and memory is 12 MB
> per worker (was 4 MB). Below ~5k rows the old scan is faster, because the
> index build cost dominates. Use the index for tables above 5k rows; below
> that, the difference is under a millisecond and not worth the memory.

**Why:** "most cases", "generally", "some situations" describe a distribution
the reader cannot see. Naming the boundary and the magnitude at both sides
turns a hedge into a rule.

## 6. Removing throat-clearing

**Before**

> ## Overview
> This section provides a high-level overview of the migration process and the
> considerations that will be discussed in the following sections. It is
> important to note that the migration has already been completed for the
> primary database, and that the following applies only to the analytics
> replica.

**After**

> ## Migration
>
> The primary database is already migrated. This covers the analytics replica
> only.

**Why:** two of the three sentences were navigation and meta-commentary. The
one fact in them ("primary is done, this is the replica") belongs in the first
line.

## 7. Converting a warning into a step

**Before**

> Note: overwriting the config file will result in the loss of any custom
> settings. You may want to back up the file first. It is generally a good idea
> to verify that your changes are correct before restarting.

**After**

> `write-config` replaces `/etc/widgets/config.yaml` entirely. Back it up
> first: `cp /etc/widgets/config.yaml{,.bak}`. Restart with
> `systemctl restart widgets`, then check
> `journalctl -u widgets -n 20` for `config loaded from /etc/widgets/config.yaml`.
> If that line is absent, the file failed to parse and the service kept the
> previous config; `journalctl` shows the parse error.

**Why:** "you may want to", "generally a good idea", and "verify that your
changes are correct" are three hedges wrapped around one backup command and one
verification command. Give the commands.

## 8. A checklist that is actually checkable

**Before**

> - Make sure the code is clean
> - Tests should pass
> - Documentation updated
> - Performance considered

**After**

> - [ ] `make lint test` exits 0
> - [ ] New behaviour has a test that fails without the change
> - [ ] `README.md` quickstart commands still run (the script in
>       `scripts/readme-check.sh` covers this)
> - [ ] Any new query has an `EXPLAIN` in the PR description, or a note on
>       why it does not need one
> - [ ] Any user-visible change has a `CHANGELOG.md` entry under Unreleased

**Why:** "tests should pass" is not checkable by a reviewer; `make lint test`
exits 0 is. Every item is a command or a diff a reviewer can see.

## 9. Hedging that is actually necessary

**Before:** "The connector usually retries idempotent operations."

**After:** "The connector retries `PUT` and `DELETE` automatically. It does
not retry `POST`: the API does not guarantee `POST` is idempotent without an
`Idempotency-Key`, so a blind retry can create a duplicate. Send an
`Idempotency-Key` if you want retries."

**Keep the qualifier when** the behaviour genuinely varies — by version,
platform, or configuration — and **name the variable**: "In Postgres < 12,
`CREATE INDEX CONCURRENTLY` …; in 12+, …". A qualified statement is precise;
an unqualified one is a guess.
