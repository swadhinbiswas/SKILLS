# Master profile

The master profile is the single source of truth about **you**: skills,
experience, preferences, salary, work authorization, and application tone.
Everything personal in this skill — match scores, resume tailoring, cover
letters, recruiter emails — is generated from this file.

**It should hold your *complete* inventory, not a curated subset.** List every
skill, tool, and technology you have, with an honest proficiency tier
(`core` = strong/production, `familiar` = working, `learning` = growing). The
matcher decides relevance *per job* — it rewards covering the skills a posting
actually asks for and penalizes the ones it asks for that you lack — so a broad
profile does not dilute or inflate your scores. Don't pre-trim it.

## Seed it from `resume.json` (recommended)

If you keep a JSON Resume–style `resume.json` (e.g. `~/JOBSCAN/resume.json`),
generate the master profile from it instead of editing two files:

```sh
python scripts/import_resume.py                       # default ~/JOBSCAN/resume.json
python scripts/import_resume.py --resume path/to/resume.json --out profile/master_profile.json
python scripts/import_resume.py --print               # preview without writing
```

It maps standard JSON Resume fields (basics, work, projects, skills, education,
languages, certificates, publications, awards) plus a custom `jobSearch` block
for search preferences. Keep `resume.json` rich; regenerate the profile when it
changes.

## Manual setup

1. Copy the example and edit it:

   ```sh
   cp profile/master_profile.example.json profile/master_profile.json
   ```

2. Fill in your real data. JSON is the default; a YAML file named
   `master_profile.yaml` / `.yml` in the same folder also works if you prefer.
3. Re-run the job search. Every posting now gets a `match_score` (0–100),
   `match_reasons`, `match_gaps`, and a skill breakdown.

The file is personal — **do not commit it** if this folder is a Git repo
(`profile/master_profile.json` is ignored by the repo's `.gitignore`).

## Fields

| Field | Meaning |
|---|---|
| `tracks` | Role tracks you want: `data_engineer`, `backend_engineer`, `software_engineer` |
| `seniority_target` | `junior`, `mid_plus`, `unclear`, `mixed` — what you'll accept |
| `preferred_countries` | ISO codes, best-first (e.g. `["de","nl","at"]`) |
| `remote_preference` | `remote`, `hybrid`, `onsite`, or `any` |
| `willing_to_relocate` | Boolean; a job needing relocation is penalized when false |
| `work_authorization` | e.g. `["eu"]`, `["uk"]`, or `["requires_sponsorship"]` |
| `languages` | Spoken languages with levels — used in tailored output |
| `salary.min` / `salary.target` | Numeric annual figure in your currency; used for salary-fit scoring |
| `skills.core` | Skills you're strong in — the highest-weighted signal |
| `skills.familiar` | Comfortable-with skills — half weight |
| `skills.learning` | Nice-to-have / growing skills — shown as matched but not weighted heavily |
| `excluded_companies` | Hard veto: matching jobs score 0 and are flagged excluded |
| `excluded_keywords` | Hard veto on title/company text (e.g. `"unpaid"`) |
| `experience[].bullets` | Your real achievements; tailoring reorders these per job |
| `application.tone` | Tone hint for generated letters/emails |
| `application.custom_note` | A sentence woven into every application (visa status, notice period, etc.) |

## How match scoring works

Each job gets points out of 100:

| Component | Max | Notes |
|---|---|---|
| Skill overlap | 40 | Job-aware: `coverage × depth`, where coverage = matched ÷ (matched + missing) against the skills *this posting* asks for, and depth rises with how many of your skills it hits. Profile size does not affect it. |
| Role track | 15 | Full points when the job's track is in your `tracks` |
| Seniority | 15 | Full when it matches your target |
| Location/remote | 15 | Preferred country + remote preference + relocation handling |
| Salary | 8 | Full when a disclosed salary meets your `target` |
| Freshness | 5 | Newer postings score slightly higher |
| Relocation support | 2 | Small bonus when the employer offers it |

Skill tiers are **weights, not filters**: everything you list is considered,
but `core` skills count double `familiar`, which count double `learning`.

Scores come with plain-English **reasons** and **gaps** (missing skills,
seniority mismatch, country mismatch, salary below target) so you can judge
each role instead of trusting a number. An excluded company or keyword forces
the score to 0 and marks the job `excluded`.
