# Report format

## Ranking

When a **master profile** is configured, each posting carries a
`match_score` (0–100) from `master_profile.py` and sorting is driven by it
(highest match first, then the hub/recency score below). Without a profile,
the hub/recency score is the only ranking. The score is composed of:

1. **Skill overlap** (40): the candidate's `core` skills count double
   `familiar` skills, against the posting title + description.
2. **Role track** (15): full points when the posting's track is one the
   candidate targets.
3. **Seniority** (15): full points when it matches the candidate's target.
4. **Location/remote** (15): preferred country, remote preference, relocation
   handling; work-authorization mismatches are flagged as gaps.
5. **Salary** (8): full when a disclosed salary meets the candidate's target.
6. **Freshness** (5): newer postings score slightly higher.
7. **Relocation support** (2).

Every score is paired with **reasons** (why it fits) and **gaps** (missing
skills, seniority/country/salary mismatches) so a number is never the whole
story. When no profile is present, the original hub/recency ranking applies:

1. **Hub bonus**: +2 for DACH/UK/Nordics/Benelux/Western-Europe hub countries,
   +1 for another EU/EEA country, +1 for an explicitly EU-eligible remote role.
2. **Recency**: +2 if posted ≤3 days ago, +1 if ≤7 days, 0 otherwise.
3. **Role/seniority confidence**: +1 for a clear Mid+ signal and +1 for a
   core title match; Junior and unclear roles remain eligible but rank below
   core Mid+ matches.
4. **Salary disclosed**: +1 when a salary is present.
5. **Relocation**: +1 when the posting mentions relocation or sponsorship; this
   is a verification signal, not a guarantee.

Sort descending by score, then by posted date (newest first).

## Recurring run

Lead with a short count:

```text
X new engineering postings since last run (Y total currently tracked)
```

Show only newly accepted postings in the chat digest. The digest is capped at
40 rows per track by default (`--max-per-track 0` disables the cap); the full
current board remains available through the local site. The report is also sent
to Telegram when configured.

## Markdown template

```markdown
## EU Software, Data & Backend Engineering Job Search — [date]

**X new engineering postings since last run** (Y total currently tracked; Junior + Mid+ by default)
_Tracks: Data Engineer X · Backend Engineer Y · Software Engineer Z | Levels: ..._

### Data Engineer

| Match | Role | Company | Location | Remote | Reloc | Level | Posted | Salary | Source | Link |
|---|---|---|---|---|---|---|---|---|---|---|
| 🟢 82 | Senior Data Engineer | Example GmbH | Berlin, DE | Hybrid | ✈️ | Mid+ | 2d ago | €70–85k | Adzuna | [apply](url) |
| 🟡 58 | Junior Backend Engineer | Example | Ireland | Remote | — | Junior | 4d ago | — | Arbeitnow | [apply](url) |

### Backend Engineer

| Match | Role | Company | Location | Remote | Reloc | Level | Posted | Salary | Source | Link |
|---|---|---|---|---|---|---|---|---|---|---|

### Software Engineer

| Match | Role | Company | Location | Remote | Reloc | Level | Posted | Salary | Source | Link |
|---|---|---|---|---|---|---|---|---|---|---|
```

The `Match` column is shown only when a profile is configured; without one
the report keeps the original `Role | Company | …` columns. Match cells use a
traffic light: 🟢 ≥75, 🟡 50–74, 🔴 <50.


Use `~` for borderline role-fit rows and `⚠ verify level` for unclear or
mixed-seniority rows. Escape pipes and collapse newlines in every table cell so
source text cannot break the table.

The `Reloc` column marks `✈️` when the posting mentions relocation support, a
relocation package, or visa/work-permit sponsorship. It is a flag to verify,
not a confirmation. Salary values are shown as reported by the source; some
boards omit currency or period.

---

Sources checked: [source status]. Countries in scope: [list]. “New” = not seen
in a prior run; undated postings are retained for review.
