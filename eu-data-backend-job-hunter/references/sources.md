# Job sources

Query every source below for the three role tracks separately
(`data engineer`, `backend engineer` / `backend developer`, and `software
engineer`). The normalizer classifies the actual title before assigning a
track, so a result's description cannot silently move it to another track.

If a source fails (timeout, rate limit, schema change), log it and continue
with the rest — never let one dead source block the whole run.

---

## 1. EURES (official EU job mobility portal) — primary, broadest EU coverage

EURES is run by the European Commission / European Labour Authority and
aggregates postings from national employment services across the EU/EEA —
the closest thing to an authoritative "every EU job" source.

**Status: unofficial/undocumented public endpoint.** There is no published,
versioned developer API. The endpoint below is the same one the EURES
website itself calls from the browser, reverse-engineered and documented by
third parties. It requires no key, but treat it as best-effort: verify it
still responds as expected on first run, and don't be surprised if the
request/response shape drifts over time.

```
POST https://europa.eu/eures/api/jv-searchengine/public/jv-search/search
Content-Type: application/json

{
  "resultsPerPage": 50,
  "page": 1,
  "sortSearch": "MOST_RECENT",
  "keywords": [{"keyword": "data engineer", "specificSearchCode": "EVERYWHERE"}],
  "locationCodes": ["de", "nl", "fr", "se", "..."],
  "requiredExperienceCodes": [],
  "publicationPeriod": null,
  "requestLanguage": "en"
}
```

- `locationCodes` takes ISO country codes; pass the EU/EEA members you want
  (or omit for EU-wide). Two-letter lowercase codes: at, be, bg, hr, cy, cz,
  dk, ee, fi, fr, de, gr, hu, ie, is, it, lv, li, lt, lu, mt, nl, no, pl,
  pt, ro, sk, si, es, se. EURES does **not** cover GB or CH — the script
  filters those out before calling EURES (`EURES_COUNTRIES` in
  `job_search.py`).
- Inspect the raw JSON response on first run — parse whatever array holds
  the job records (commonly nested under a results/content-style key) and
  adjust the parser in `job_search.py` if the field names differ from what's
  stubbed in there.
- Re-page with `"page": 2, 3, ...` until an empty page comes back.

---

## 2. Adzuna — secondary, has salary data, needs a free key

Covers (as of last check) roughly a dozen+ country indexes including several
EU members: Germany, France, Poland, Austria, Netherlands, Italy, Spain,
Belgium, plus UK/CH/non-EU. Good salary-range data where advertisers provide
it.

**Sign up free**: https://developer.adzuna.com → get `app_id` + `app_key`.

```
GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
    ?app_id={APP_ID}
    &app_key={APP_KEY}
    &results_per_page=50
    &what=data%20engineer
    &max_days_old=14       # default freshness window; tune per run
    &sort_by=date
    &content-type=application/json
```

`{country}` is the two-letter code, one call per country in scope (the
script does this automatically). The currently verified Adzuna indexes used by
this skill are: `at`, `be`, `ch`, `de`, `es`, `fr`, `gb`, `it`, `nl`, and `pl`.
Other countries are covered by the other sources; unsupported indexes are
skipped rather than generating repeated 404 warnings.
Response JSON: `results[]` with `title`, `company.display_name`,
`location.display_name`, `redirect_url`, `created` (ISO date), `description`
(truncated), `salary_min`, `salary_max`.

---

## 3. Arbeitnow — free, no key, strong DACH/EU tech coverage

Public, no-auth JSON API, well suited to automation and safe to poll weekly.
It is a shared/global feed, so the pipeline applies the requested country
scope after normalization. Strong signal for visa-sponsorship and
remote-friendly EU tech roles.

```
GET https://www.arbeitnow.com/api/job-board-api
GET https://www.arbeitnow.com/api/job-board-api?page=2   # paginate until empty
```

Response: `data[]` with `title`, `company_name`, `tags[]`, `job_types[]`,
`location`, `remote` (bool), `url`, `description`, `created_at` (unix
timestamp), `slug`. No dedicated seniority field — classify from
title/description per `seniority-rules.md`. Filter client-side for the two
role tracks and EU locations (many postings are DE-specific; `remote: true`
ones may be open EU-wide — check description for region restrictions).

---

## 3b. Additional EU-focused sources (added in v3)

These run on every search by default and broaden coverage well beyond the
original four. All fail soft like the others.

- **Remotive** — `GET https://remotive.com/api/remote-jobs?limit=50&search=<keyword>`
  (free, no key). `jobs[]` with `title`, `company_name`,
  `candidate_required_location`, `url`, `publication_date`, `salary`,
  `category`, `description` (HTML). Remote-only; the EU-eligibility check in
  `_remote_eu_ok` rejects US/APAC-only postings.
- **The Muse** — `GET https://www.themuse.com/api/public/jobs?page=N&category=<cat>`
  (free, no key). We call the `Software Engineering` and `Data and Analytics`
  categories, up to 3 pages each. `results[]` gives `name`, `company.name`,
  `locations[].name`, `levels[].name`, `refs.landing_page`,
  `publication_date`, `contents`. Levels are mapped to seniority prefixes
  (`Senior Level` → `Senior`, `Entry Level` → `Junior`). Most postings are
  US; the country-scope filter drops them.
- **Landing.jobs** — `GET https://landing.jobs/api/v1/jobs?limit=50&offset=N`
  (free, no key; EU/Portugal-centric). Array of records with `title`,
  `locations[].country_code`, `remote`, `gross_salary_low/high`,
  `currency_code`, `role_description`, `main_requirements`. The company name
  is derived from the URL's second-to-last path segment (the API omits it).
  Paginate with `offset` (the API ignores `page`).
- **Jooble** — `POST https://jooble.org/api/<API_KEY>` with
  `{"keywords": "...", "location": ""}`. Requires a free key
  (`JOOBLE_API_KEY` in `.env` or `--jooble-key`); skipped without one.
- **HN "Who is hiring?"** — *opt-in* via `--include-hn`. Finds the latest
  `whoishiring` story via the Algolia API
  (`search_by_date?tags=story,author_whoishiring`) and parses its top-level
  comments. The format is free text, so it is deliberately conservative: a
  comment is kept only when its headline classifies as a target track *and*
  it names an in-scope country or EU-eligible remote. Expect noise; it is off
  by default for that reason.

---

## 4. Remote-job boards — optional, for EU-eligible remote roles

All free, public, no key required. Useful supplement since a meaningful
share of Software/Data/Backend Engineer hiring is now remote-first. The script
keeps a remote posting only when its location or description explicitly names
Europe, the EU, EMEA, or an in-scope country. Bare `Remote`, `Anywhere`,
`Worldwide`, and `Global` labels are rejected because they usually mean
globally competitive rather than EU-eligible.

- `GET https://remoteok.com/api` — first array element is metadata, skip it.
- `GET https://jobicy.com/api/v2/remote-jobs` — the shared feed is fetched once
  per run and classified locally into Data/Backend/Software. The current
  `jobDescription`, `jobExcerpt`, `jobLevel`, salary fields, and location are
  consumed when present.
- `GET https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss`
  (also `.../remote-full-stack-programming-jobs.rss` and
  `.../remote-devops-sysadmin-jobs.rss`) — We Work Remotely's public RSS
  feeds. Titles come back as `Company: Job Title`; each item has an explicit
  `<region>`/`<country>`. The script parses these with stdlib
  `xml.etree`. Track (Data vs Backend) is inferred from the title.
- ~~`GET https://himalayas.app/jobs/api`~~ — **not used**: the public
  endpoint ignores query/category filters (returns the same ~20 unrelated
  postings every time), so it's not worth the noise.

---

## 5. LinkedIn / Indeed / Glassdoor / company career pages — supplementary only

No free bulk API, and automated scraping breaches their terms of service —
don't do it. Instead, use the agent's normal web-search capability for a
handful of *targeted* top-up queries per run (e.g. `"data engineer" Berlin
site:linkedin.com/jobs`), read a few individual result pages if genuinely
useful, and fold anything relevant into the same normalized schema. Keep
this to a small, occasional top-up — it's not the reliable, repeatable core
of the pipeline the way the four sources above are.

---

## 6. Turso (libsql) — output database for the job board site

Not a job source — it's where every run stores the full normalized postings
so the web UI (`site/`) can browse them. Implemented in `scripts/turso.py`,
which talks to Turso's HTTP `/v2/pipeline` endpoint (stdlib only; no libsql
client install needed).

- **URL**: `libsql://<your-db>.<region>.turso.io` (set as `TURSO_URL`). HTTP endpoint is the same host with `https://`.
- **Auth**: `TURSO_TOKEN` = a read-write database token (kept in `.env`,
  never sent to the browser — `scripts/server.py` proxies `/api/*`).
- **Table**: `jobs(id PRIMARY KEY, title, company, location, country,
  eligible_countries, remote, url, source, posted, salary, snippet, description,
  role_fit, seniority, relocation, track, company_url, match_score,
  match_reasons, match_gaps, matched_skills, missing_skills, excluded,
  first_seen, last_seen, active, created_at, updated_at)` + indexes on
  track/country/source/posted/last_seen/remote/match_score. `eligible_countries`
  preserves multi-country and Europe-wide remote roles for country filters.
  `track` is one of `data_engineer`, `backend_engineer`, or
  `software_engineer`; rows rejected by the current quality filters are marked
  inactive after a successful sync and grace period. The `match_*` columns hold
  the personalized profile score and its reasons/gaps/skills.
- **Applications table**: `applications(job_id PRIMARY KEY, status, method,
  recipient, subject, notes, resume_path, cover_path, email_path, match_score,
  applied_at, created_at, updated_at)` tracks each draft/application through
  `draft → ready → applied → interview → offer/rejected/withdrawn`.
- **Write path**: `job_search.py` upserts all current postings each run
  (idempotent, keyed on a canonical-URL ID). A source failure or partial upsert
  is surfaced; stale rows are hidden only after a complete successful snapshot
  and a seven-day grace period.
- **Note on values**: Turso's HTTP API wants an internally-tagged Value enum
  (`{"type": "text"|"integer"|..., "value": ...}`) for bind params, and
  returns cells in the same envelope — `turso.py` handles both directions.
