# Role relevance & seniority filtering

Apply role relevance first, then seniority. The classifier is deliberately
**title-first**: the search keyword is not evidence of a posting's track. A
backend title with a data-heavy description stays Backend; a generic Software
Engineer title is accepted only when its description shows software work and is
not frontend-only.

## 1. Role tracks

### Data Engineer — core title matches

- `data engineer`, `data engineering`
- `data platform engineer`, `data warehouse engineer`
- `ETL engineer/developer`, `database engineer`, `big data engineer`

Accept adjacent titles such as analytics, ML/MLOps, data infrastructure, and
platform engineering only when the description shows pipelines, warehouses,
Spark, dbt, Kafka, SQL, or comparable engineering work. Tag these rows as
borderline (`~`).

### Backend Engineer — core title matches

- `backend engineer`, `back-end engineer`, `backend developer`
- `software engineer, backend`, `backend software engineer`
- `API engineer`

Accept SRE, platform, and full-stack titles as borderline only when the
description shows backend services, APIs, distributed systems, queues, or
service infrastructure.

### Software Engineer — core title matches

- `software engineer`, `software engineering`, `software developer`
- `application developer`, `full-stack engineer`

A generic Software Engineer posting is included as borderline when the
description is not clearly frontend-only. Backend- or data-heavy descriptions
are marked core. Frontend-only, mobile-only, QA/test-only, data analyst, BI,
data scientist, product, community, and DevOps-only titles are excluded.

Do not infer a track from a broad search query. The normalized title and
snippet decide the track.

## 2. Seniority bands

The default search keeps **Junior + Mid-level and above + Unclear/Mixed** roles.
It excludes internships, trainees, apprenticeships, working students, and
thesis/masters projects unless `--include-internships` is passed.

### Internship / trainee — hard exclusion

English: `intern`, `internship`, `trainee`, `working student`, `apprentice`,
`apprenticeship`, `thesis`, `master thesis`.

German: `werkstudent`, `praktikant`, `praktikum`, `ausbildung`.

French: `stagiaire`, `alternance`, `débutant` when clearly paired with a
training/entry context.

Spanish/Italian: `becario`, `prácticas`, `tirocinante`, `neolaureato`.

These signals take precedence over words such as “senior” in the description.
A posting titled `Internship - Data Engineer` is not an unclear role merely
because the description says it works with senior engineers.

### Junior

`junior`, `jr.`, `entry-level`, `graduate`, `new grad`, `berufseinsteiger`,
`0–1 years`, `0–2 years`, `no experience required`, and equivalent localized
entry signals.

Junior roles are included by default. A title containing both junior and
mid/senior language is classified `mixed` and shown for manual review.

### Mid-level and above

`mid`, `mid-level`, `intermediate`, `confirmed`, `2+ years` or more, `senior`,
`sr.`, `staff`, `principal`, `lead`, `head of`, `architect`, `engineering
manager`, and `Engineer II/III/IV`.

The report displays this band as `Mid+`. Title signals are authoritative;
description signals are used only for explicit role-level or experience
requirements such as `minimum 3 years`, not incidental phrases like “senior
leadership team”. Unclear postings remain visible with `⚠ verify level` rather
than being silently discarded.

## 3. Freshness and remote eligibility

Dated postings older than `--max-age-days` (default 14) are removed. Undated
postings are retained because some feeds omit dates.

For remote boards, `Remote`, `Anywhere`, `Worldwide`, or `Global` alone is not
enough. The posting must mention Europe, EU, EMEA, or an in-scope country in
its location or description. This avoids importing globally competitive roles
that are not actually open to EU/EEA candidates.
