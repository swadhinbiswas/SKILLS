---
name: eu-data-backend-job-hunter
description: Runs a quality-controlled multi-source job search for Software Engineer, Data Engineer, and Backend Engineer roles across the EU/EEA + UK/CH (plus explicitly EU-eligible remote), keeps Junior and Mid-level+ roles while excluding internships by default, and posts a ranked, deduplicated report to the user's Telegram channel. Use this skill any time the user says "find job", "find jobs", "find me a job", "job search", "job hunt", "job alert", "weekly/daily job digest", "any new jobs this week", "scan/refresh/check jobs", or asks to search for EU software, data, or backend engineering jobs — even if they don't say the word "skill".
---

# EU Software / Data / Backend Engineering Job Hunter

A repeatable pipeline that queries multiple job sources, normalizes results,
filters for **Software Engineer, Data Engineer, and Backend Engineer** roles in
the **EU/EEA + UK/CH + explicitly EU-eligible remote** market, keeps **Junior
and Mid-level+** roles by default, excludes internships/trainees, dedupes
against prior runs, scores every posting against your **master profile**, and
outputs a ranked Markdown report. It can then **tailor a resume, cover letter,
and recruiter email** for any posting (drafts only — nothing is sent for you)
and track each application. Designed to be run by any coding agent with
shell/Python access — nothing here is model-specific.

**Honesty note, read this first:** no single source has 100% of EU job
postings. LinkedIn, Indeed, and Glassdoor do not offer free bulk APIs, and
scraping them programmatically violates their terms of service — this skill
does not do that. Instead it combines several legitimate free/keyed APIs
(broadest legal coverage available) plus optional targeted web-search
top-ups. Treat "every job" as "every job across these sources," and mention
that framing to the user the first time you run this.

**Second honesty note:** the application workflow is **drafts only**. It writes
a tailored resume, cover letter, and recruiter email, then stops. It never
auto-submits applications, never scrapes LinkedIn/Indeed for contacts, and
never sends messages on the user's behalf — that breaches those sites' terms,
harms the user's reputation with employers, and is not something this skill
will do. The board/CLI prepare everything; the user reviews and sends.

## Step-by-step workflow

1. **Read the reference files below before your first run** — they contain
   the exact endpoints, params, and filtering rules. Don't guess at API
   shapes from memory.
2. **Load prior state.** Read `state/seen_jobs.json` (created on first run)
   to know which job IDs/URLs were already reported.
3. **Query every source** in `references/sources.md` for all three role tracks
   (Software Engineer, Data Engineer, Backend Engineer) across the target
   countries. Use `scripts/job_search.py`; it classifies the actual title
   before assigning a track.
4. **Normalize** every result into the common schema (see script/output:
   title, company, location, country, remote flag, URL, source, posted date,
   salary if present, description snippet, role fit, seniority, relocation
   flag). Strict source failures are surfaced and prevent destructive board
   pruning; a partial source response never silently becomes a complete
   snapshot.
5. **Filter for relevance**: explicit title matches win; generic Software,
   platform, analytics, ML/MLOps, SRE, and full-stack titles require supporting
   engineering context. Reject frontend-only, mobile-only, QA/test-only, data
   analyst/BI, data scientist, product, community, and DevOps-only roles.
6. **Filter for seniority and freshness**: keep Junior and Mid+ by default;
   keep unclear/mixed rows with a warning; exclude internships/trainees unless
   `--include-internships` is set; drop dated jobs older than 14 days by default.
7. **Dedupe**: canonicalize tracking URLs, preserve URL aliases in the state
   file, and merge cross-board duplicates while keeping distinct same-source
   requisitions. Compare against `seen_jobs.json`; never create a second record
   merely because two boards describe the same posting.
8. **Rank** remaining new postings — see scoring notes in
   `references/report-format.md`.
9. **Write the report** using the template in `references/report-format.md`
   and show it to the user directly in chat (don't just save silently). If
   Telegram is configured (see "Sending the report to Telegram" below), the
   **ranked digest is also posted there automatically** at the end of every
   run; the complete current set remains in the board.
10. **Update state**: after a complete source snapshot, append the accepted job
    IDs and URL aliases to `seen_jobs.json` with today's date. If any source is
    incomplete, leave the state unchanged so the next run retries the missing
    results. A state-file lock prevents concurrent runs from racing.
11. **If this is a recurring/scheduled run** (see "Running this on a
    schedule" below), keep the message short: lead with "N new roles since
    last run," and only show full source-by-source detail on request.
12. **Offer to draft applications** for the strongest matches: `scripts/apply.py`
    (or the board's **Tailor application pack** button) writes a tailored
    resume, cover letter, and recruiter email. Drafts only — never send on the
    user's behalf.

## Defaults for this user (confirmed by user, override if they say otherwise)

- **Countries**: broadest EU/EEA coverage **plus UK (GB) and Switzerland
  (CH)**, since they're English-friendly tech hubs. Surface DACH
  (DE/AT/CH), UK, Nordics (SE/DK/FI/NO where applicable), Benelux
  (NL/BE/LU), and Western Europe (FR/IE/ES) first in the report; still
  include other EU/EEA countries, just lower in the ranking.
- **Remote**: include roles explicitly open to Europe/EU/EMEA or an in-scope
  country. Bare `Remote`, `Anywhere`, `Worldwide`, and `Global` labels are
  rejected unless the description supplies an EU eligibility hint.
- **Seniority**: Junior + Mid-level and above by default. Unclear/mixed levels
  stay visible with a warning. Internships, trainees, apprenticeships, working
  students, and thesis projects are excluded unless explicitly requested.
- **Freshness**: dated postings from the last 14 days by default; undated
  postings remain visible for manual review.
- **Cadence**: weekly. Design the state file and report framing around a
  weekly digest, but the script works fine run on-demand too.

## Files in this skill

- `references/sources.md` — every data source (EURES, Adzuna, Arbeitnow,
  Remotive, The Muse, Landing.jobs, Jooble, optional HN "Who is hiring", plus
  RemoteOK/Jobicy/We Work Remotely), exact endpoints/params, auth
  requirements, and known limitations. Read this before calling any API.
- `references/seniority-rules.md` — title-first role classification,
  seniority bands, hard internship exclusions, freshness, and EU remote
  eligibility rules.
- `references/report-format.md` — the exact Markdown report template and
  ranking/scoring logic for all three tracks (profile match score + fallback).
- `profile/master_profile.example.json` — a fill-in master profile template.
  Copy to `profile/master_profile.json`; see `profile/README.md`.
- `templates/resume.html.j2` — print-ready CV template used for the tailored PDF.
- `tests/test_job_search.py` — deterministic regression tests for track
  classification, seniority handling, freshness, remote eligibility, dedupe,
  report escaping, and the new source parsers.
- `tests/test_master_profile.py` — profile loading, skill normalization,
  gap detection, and match scoring.
- `tests/test_apply.py` — resume/cover-letter/email tailoring output.
- `tests/test_telegram_formatter.py` — checks the Telegram export parser against
  Software, level-labelled, and legacy lines.
- `tests/test_turso.py` — schema migration, strict partial-upsert failures, and
  application tracking. Run `python -m unittest discover -s tests -v`.
- `scripts/job_search.py` — fetch, normalize, classify, filter, freshness,
  dedupe, profile scoring. Stdlib only. Run `python scripts/job_search.py --help`.
- `scripts/master_profile.py` — loads the master profile and computes the
  explainable 0–100 match score plus skill gaps.
- `scripts/import_resume.py` — converts a JSON Resume–style `resume.json`
  (basics, work, projects, skills, education, languages, certificates,
  publications, awards + a `jobSearch` block) into `profile/master_profile.json`
  so the resume seed is the single source of truth.
- `scripts/apply.py` — generates the tailored resume (MD/HTML/PDF), cover
  letter, and recruiter-email draft for a job. **Drafts only.**
- `scripts/turso.py` — stdlib Turso (libsql) HTTP client: schema init, job
  upserts, application tracking, query helper.
- `scripts/server.py` — serves the **job-board site** and proxies `/api/*` to
  Turso (token stays server-side). Now also exposes `/api/profile`,
  `/api/applications`, `/api/tailor`, and `/api/draft/<id>/<file>`. Run
  `python scripts/server.py --port 8787`.
- `site/` — the web frontend: `index.html`, `styles.css`, `app.js`. Job cards
  with apply links, match-score badges, search/filter/sort (incl. by match),
  "hide applied", pagination, a full-detail modal with match reasons/gaps, a
  **Tailor application pack** button, and a tracker for your applications.
- `applications/` — generated application packs, one folder per job id
  (resume, cover letter, email draft, snapshot). Ignored by git.
- `state/seen_jobs.json` — persistent dedupe store and URL-alias map, created
  on first run. **This file must persist between runs** (don't let it live in
  a temp dir that gets wiped) — that's what makes "weekly" actually mean
  "what's new" instead of re-showing the same jobs every time.

## Master profile & match scoring

The pipeline is personalized by a single **master profile** file. Put it at
`profile/master_profile.json` (a YAML version in the same folder also works).
Generate it from a JSON Resume–style seed with
`python scripts/import_resume.py` (default `~/JOBSCAN/resume.json`), copy
`profile/master_profile.example.json` to start, or write it by hand;
field-by-field docs live in `profile/README.md`.

The profile is a **complete inventory** of your skills, experience, and
preferences — list everything, with an honest core/familiar/learning tier. The
matcher decides relevance per job; it is not a curated shortlist.

When a usable profile is present, `job_search.py` scores every posting:

- `match_score` — 0–100, blending skill coverage (job-aware: the skills *this
  posting* asks for, weighted core > familiar > learning, minus the ones you
  lack), role track, seniority fit, country/remote preference, salary fit,
  freshness, and relocation support. The profile holds your *complete* skill
  inventory; relevance is decided per job, so profile size never inflates or
  dilutes a score.
- `match_reasons` — plain-English "why it fits" lines.
- `match_gaps` — missing skills, seniority/country/salary mismatches, work-
  authorization concerns.
- `matched_skills` / `missing_skills` — the skill breakdown.

The report gains a traffic-light `Match` column (🟢 ≥75, 🟡 50–74, 🔴 <50) and
sorting is driven by match score. Jobs from `excluded_companies` or matching
`excluded_keywords` are hard-vetoed (`match_score` 0, `excluded=1`) and dropped
before the report and database. With **no profile**, everything degrades
cleanly to the original hub/recency ranking, so nothing breaks.

Override or disable with `--profile <path>` / `--no-profile`.

## Application drafting (drafts only)

Given a job and the profile, `scripts/apply.py` writes a tailored application
pack to `applications/<job_id>/`:

- `resume.html` + `resume.pdf` (via headless Chromium/pandoc) + `resume.md` —
  skills and experience bullets reordered so the ones this posting asks for
  come first.
- `cover_letter.md` / `cover_letter.txt` — referencing the matched skills and
  your most relevant achievement.
- `email.txt` — `To:`/`Subject:`/body for a recruiter email, including best-
  effort recipient extraction from the posting text, plus a match/gap footer
  for your interview prep.
- `job.json` / `meta.json` — the posting snapshot and a generation manifest.

**This is deliberately drafts-only.** Nothing is ever sent. Automated mass
applications breach most boards' terms of service and damage your reputation,
and scraping LinkedIn/Indeed for contacts or auto-DMing recruiters is out of
scope by design. You review each draft and hit send yourself.

```sh
python scripts/apply.py tailor --job-id <id>          # pull the job from Turso
python scripts/apply.py tailor --job-json job.json    # or a local snapshot
python scripts/apply.py list                          # recent drafts/applications
python scripts/apply.py status --job-id <id> --status applied
```

Every draft is recorded in the `applications` table with status
`draft → ready → applied → interview → offer/rejected/withdrawn`, and shows up
on the board's **📁 Applications** tracker.

## Job board site (Turso database + web UI)

Every run also syncs the full set of current postings to a Turso (libsql)
database, which a small static-site server reads back for a browsable job
board. Flow:

1. **On every run**, `job_search.py` upserts all current postings into the
   `jobs` table via `scripts/turso.py` (idempotent — re-runs update `last_seen`
   instead of duplicating). Config is read from `.env` / env vars:
   - `TURSO_URL` — e.g. `libsql://<db>.<region>.turso.io`
   - `TURSO_TOKEN` — the database auth token (read-write)
   - or CLI flags `--turso-url` / `--turso-token`.
2. **Start the site** (keeps the token off the browser; the frontend only
   talks to `/api/*` on the local server):
   ```
   python scripts/server.py --port 8787
   ```
   Then open http://localhost:8787.
3. The DB stores full details for each job: title, company, location, country,
   eligible countries for multi-country/Europe-wide roles, remote/relocation
   flags, apply URL, company website (when the source provides one), salary,
   source, seniority, role track, full description snippet, and first/last-seen
   dates.

Site features: stat cards (total / updated-today / remote / relo / per-track),
free-text search, filters (track, level, country, source, remote-only,
relo/visa, minimum match score, hide-applied), sorting (newest / **match
score** / oldest / company), pagination, clickable **Apply** links, a
company-site or company-search link, match-score badges, and a full-detail
modal with the description, match reasons, and gaps. From the modal you can
**Tailor application pack** (generates the resume/cover-letter/email drafts and
links them for download) and set the application status; the **📁 Applications**
tracker lists every draft and its state. The board contains only rows accepted
by the current quality filters; stale or rejected rows are hidden after a
successful sync and a seven-day grace period. Every job must still be verified
against the original posting before applying.

If the DB sync fails it only logs a warning — the Telegram report and stdout
report still work.

## Sending the report to Telegram

The ranked digest is pushed to a Telegram bot/channel automatically at the end
of every run (as long as it's configured). Setup:

1. Create a bot with @BotFather and grab its token.
2. Add the bot as an **admin** of the target channel — e.g. the public channel
   `@dataengineeringjob4u` (a bot can't post to a channel it isn't an admin
   of).
3. Set two values via CLI flags, env vars, or a local `.env` file in this
   folder (`scripts/job_search.py` loads `.env` automatically; real env vars
   override it):
   - Token: `--telegram-token` / `TELEGRAM_BOT_TOKEN`
   - Destination: `--telegram-chat` / `TELEGRAM_CHAT_ID` — accepts a public
     channel `@username`, a `https://t.me/...` channel URL, or a numeric chat
     id (for a private chat/group, get the id from @userinfobot).

```
# .env (already created for this user, keeps the token out of shell history)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=@dataengineeringjob4u
```

Details of the delivery:

- Telegram doesn't render markdown tables, so the channel copy uses a
  Telegram-HTML format instead of the markdown report: bold title header,
  bold/emoji section markers (`🛠️ Data Engineer`, `⚙️ Backend Engineer`,
  `💻 Software Engineer`), one clean bullet per job (`• **Role** · (Level) ·
  Company · Location · Remote/On-site · date · salary · Apply`), and clickable
  `<a>` apply links. Unclear seniority gets a `⚠️`, borderline fits a `~`.
  The digest is capped per track by default; the complete set stays in the
  board. The markdown version still goes to stdout.
- If the report exceeds Telegram's 4096-character per-message limit, it's
  split across multiple messages (on line boundaries, so HTML tags are never
  cut in half), and nothing gets dropped.
- If only the token is set but no chat is configured, the script prints a
  warning and skips the send — the report always still goes to stdout.

## Running this from opencode ("just tell it to find job")

This skill is registered with opencode via `skills.paths` in
`~/.config/opencode/opencode.jsonc` (it points at `/home/swadhin/skills`, so
this folder is scanned recursively for `SKILL.md`). Once opencode is
restarted, the user can simply type something like **"find job"**,
**"find jobs"**, or **"any new jobs this week"** and the agent will load this
skill, run `python scripts/job_search.py`, and post the report to Telegram —
no manual commands needed. The `.env` file keeps the bot token and channel
id available to the script without shell env vars.

If the skill stops being detected, check: (1) `skills.paths` still points at
this folder in `~/.config/opencode/opencode.jsonc`, and (2) opencode was
restarted after the config change (config is loaded once at startup).

## Quality controls and commands

The default command keeps Junior + Mid+ + Unclear/Mixed and excludes
internships:

```sh
python scripts/job_search.py --weekly
```

Useful overrides:

```sh
# Mid-level and senior only
python scripts/job_search.py --levels mid_plus

# Include internships/trainees when you explicitly want them
python scripts/job_search.py --include-internships

# Keep a wider 30-day window
python scripts/job_search.py --max-age-days 30

# Keep the chat/Telegram digest concise (0 means no cap)
python scripts/job_search.py --max-per-track 40

# Disable dated-post freshness filtering
python scripts/job_search.py --max-age-days 0

# Use a specific master profile (or ignore it entirely)
python scripts/job_search.py --profile profile/master_profile.json
python scripts/job_search.py --no-profile

# Add the Jooble source (free key) and the noisier HN hiring thread
python scripts/job_search.py --jooble-key <key> --include-hn
```

Application drafting (run after a search, from the board or the CLI):

```sh
python scripts/apply.py tailor --job-id <id>       # resume + cover letter + email
python scripts/apply.py list                       # what have I drafted/applied to
python scripts/apply.py status --job-id <id> --status applied
```

The classifier is title-first. A result is assigned to Data, Backend, or
Software from its title and supporting description, never from the search
keyword. A generic Software Engineer role is kept as borderline unless it is
clearly frontend-only. Bare worldwide remote labels are rejected unless the
posting explicitly mentions Europe/EU/EMEA.

When upgrading from an older filter policy, run once with a new state file
(for example `--state-file state/seen_jobs.v2.json`) so postings rejected by
the old classifier are not treated as already reported. Keep the old state
file as a backup until the corrected report has been reviewed.

## Running this on a schedule

Pick whichever fits the agent/environment in use:

- **opencode recurring tasks**: schedule this skill to run weekly so the
  digest is posted to the channel automatically.
- **Claude Code / Cowork**: use the environment's scheduled/recurring task
  feature to run this skill weekly and post the digest to the user.
- **Cron (any machine)**: `0 8 * * 1 cd /path/to/skill && TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=@dataengineeringjob4u python
  scripts/job_search.py --weekly >> logs/run.log 2>&1` (Mondays 8am) — the
  script posts the ranked digest to Telegram itself, no extra messenger needed.
- **GitHub Actions**: a scheduled workflow (`on: schedule: cron: '0 8 * * 1'`)
  that runs the script with `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` set as
  repo secrets, so the report is posted to the channel automatically.
- **On-demand only**: just re-run `job_search.py` whenever asked; the state
  file still ensures only genuinely new postings get highlighted.

Whichever method: the state file and this skill folder must live somewhere
persistent (not wiped between runs), or dedup breaks and every run looks
like week one again. The same scheduled run also upserts the DB (via `.env`
`TURSO_URL`/`TURSO_TOKEN`), so the job-board site stays fresh with no extra
step — just keep `scripts/server.py` running.

## First-run checklist

- [x] Get a free Adzuna API key at https://developer.adzuna.com (App ID +
      App Key) — everything else needs no signup. Keys are set in `.env`
      (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`) and verified working.
- [ ] Confirm the EURES endpoint in `references/sources.md` still responds
      as documented — it's a public but *unofficial* endpoint (the same one
      the EURES website's own frontend calls), not a stable published API,
      so it can change without notice. If it breaks, skip it gracefully and
      lean on Adzuna + Arbeitnow + the remote boards; don't let one source
      failing kill the whole run.
- [ ] Do one manual run and sanity-check a handful of results against the
      actual job boards before trusting the weekly digest. Check all three
      tracks, at least one Junior and one Mid+ result, and verify that no
      internship or out-of-scope remote role appears.
- [ ] Run the regression suite after classifier/filter changes:
      `python -m unittest discover -s tests -v`.
- [ ] Confirm Telegram delivery: bot added as admin of the channel (e.g.
      `@dataengineeringjob4u`), `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` set,
      and one test run posts the ranked digest to the channel.
- [x] Confirm Turso sync + site: `TURSO_URL` / `TURSO_TOKEN` in `.env`, a run
      upserts into the `jobs` table, hides stale rows after a fully successful
      sync, and `python scripts/server.py --port 8787` serves the board
      (Software/Data/Backend stats, level filter, search, detail modal all
      verified working).
- [ ] Create `profile/master_profile.json` (copy the example) with real skills,
      experience, and preferences; re-run and confirm the report shows a
      `Match` column and sensible reasons/gaps.
- [ ] Generate one application pack (`python scripts/apply.py tailor --job-id <id>`)
      and read the resume/cover letter/email end to end before trusting it.
- [ ] Run the full suite (`python -m unittest discover -s tests -v`) — 54
      tests cover classification, sources, profile scoring, tailoring, Turso
      schema/application tracking, and Telegram formatting. CI runs it on
      every push via `.github/workflows/tests.yml`.
