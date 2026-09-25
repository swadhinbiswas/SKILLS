---
name: job-application-matcher
description: Evaluates a job posting against the user's resume, decides whether it's worth applying to, and if it fits  saves a fully-detailed entry to Notion (or any other connected data-push MCP, like Airtable or Coda) with requirements, how to apply, and a drafted personalized cover letter or application email. Use this whenever the user pastes a job posting (from LinkedIn, Indeed, Greenhouse, Lever, Wellfound, a company careers page, or anywhere else), pastes a job URL, asks "does this job fit me", "should I apply to this", "add this to my job tracker", "check this against my resume", or wants a job logged, scored, or turned into ready-to-send application material. Trigger this even if the user doesn't mention a resume by name, as long as one has been shared earlier in the conversation or session.
---

# Job Application Matcher

## What this skill does

Given a job posting and the user's resume, this skill produces a grounded fit verdict, logs the job to wherever the user keeps their job search (Notion by default, but any connected data-push MCP works), and drafts the actual application material — so the user's job search output is "here's a ready row in their tracker and a cover letter to send," not just "here's my opinion."

The job posting can come from **any site** — paste the text, paste a link, doesn't matter. Everything downstream works off the extracted content, not the source.

## Before you start

**You need the resume once.** If the user hasn't shared it yet in this session, ask for it before doing anything else — paste, upload, or a link all work. Once you have it, hold onto it for the rest of the session; don't ask again for every new posting.

**You need a place to push results.** Check available tools for a connector that can write structured records somewhere persistent — Notion is the common case, but Airtable, Coda, or any similar database-style MCP works the same way. If nothing is connected, say so plainly, still do the full analysis in chat, and don't pretend something was saved when it wasn't.

Don't guess at either of these — a wrong resume match or a job silently not saved is worse than asking once.

## Step 1 — Get the posting

- Pasted text: use it as-is.
- A URL: fetch it. If the fetch comes back empty or login-walled (common on LinkedIn and some ATS pages), tell the user and ask them to paste the text instead of guessing at what the page might say.
- Never fill in job details from general knowledge of the company or role — if the posting doesn't say it, it's "not stated," not inferred.

## Step 2 — Extract the job, structured

Pull out, marking anything absent as "not stated":

- Title, company, location, work mode (remote / hybrid / onsite), employment type
- Salary or comp range
- Seniority level
- Must-have requirements (list)
- Nice-to-have / preferred qualifications (list)
- Responsibilities (short summary, not a copy of the posting)
- How to apply — email address, apply URL, or both
- Deadline, if any
- Whether a cover letter is explicitly requested
- Anything else that changes whether the user should bother: visa sponsorship stance, required assessment, portfolio/work-sample ask, referral requirement

## Step 3 — Compare against the resume, honestly

Sort each must-have and nice-to-have into **Matched**, **Partially matched**, or **Missing**, based only on what the resume actually says. A tangential skill isn't a match — if the posting wants "production Kubernetes experience" and the resume mentions Docker once, that's partial, not matched.

Give one overall verdict:

- **Strong fit** — nearly all must-haves matched
- **Worth applying** — most must-haves matched, one or two gaps that are common/learnable
- **Long shot** — several must-haves missing, but not disqualifying (wrong years of experience, missing one core tool)
- **Not a fit** — missing multiple hard requirements, or a structural mismatch (wrong seniority tier, wrong location with no remote option, wrong domain entirely)

Say *why* in a sentence or two — the verdict alone isn't useful without the reasoning.

## Step 4 — Decide whether to log it

- **Strong fit / Worth applying** → proceed straight to Step 5, no need to check in first.
- **Long shot** → ask the user once whether they still want it tracked; don't assume.
- **Not a fit** → skip Step 5 by default. Still give the verdict and reasoning (Step 7) so the user learns what to look for. Only log it if the user says they want a record of jobs they considered and passed on.

## Step 5 — Push it to the tracker

Use whatever push-capable connector is available. If a job database already exists (ask the user once, on first use, where it lives — which Notion page or workspace, or equivalent — and reuse that answer for every job after), match its existing property names where possible. Otherwise create these properties:

| Property | Type | Value |
|---|---|---|
| Job Title | title | extracted title |
| Company | text | extracted company |
| Status | select | "To Apply" |
| Fit | select | Strong fit / Worth applying / Long shot |
| Location | text | extracted location / work mode |
| Salary | text | extracted range, or "Not stated" |
| Apply By | date | deadline, if any |
| Apply URL | url | apply link |
| Apply Email | email | apply address |
| Cover Letter Needed | checkbox | yes/no |
| Source | url | the original posting link |

Before creating a new record, check whether one already exists for the same company + title, and update it instead of creating a duplicate.

In the page body of the record, include:

- The requirements checklist from Step 3 (Matched / Partial / Missing)
- The responsibilities summary
- Any special instructions (assessment, referral, portfolio)
- The drafted cover letter / email from Step 6

## Step 6 — Draft the actual application material

- If the posting asks for a cover letter, or the user wants one regardless of what's asked, draft one grounded strictly in the resume — no invented employers, dates, titles, or achievements. Three to four short paragraphs, specific to this role, built around the two or three strongest matches from Step 3.
- If the posting takes applications by email, also draft the email itself — subject line and body, ready to send.
- Save both into the tracker record from Step 5 (its own section or a linked sub-page) and also show them to the user in chat.
- This skill drafts; it never sends. The user reviews and sends it themselves, or explicitly asks you to send it through a connected mail tool if one exists — never do it by default.

## Step 7 — Close with a short, concrete summary

Every run ends with this, not a wall of restated data:

- The verdict and the one-line reason
- What's genuinely missing from the resume for *this* role, if anything
- Exactly how to apply — email, URL, or both — and whether a cover letter is expected
- A link to the tracker record, if one was created
- Whether a cover letter / email was drafted and where to find it

## Edge cases

- **Several postings pasted at once** — process each separately, one tracker record per job, one summary per job.
- **No resume yet** — get it before anything else; don't produce a verdict without one.
- **Vague posting** — report what's missing rather than assuming it in the user's favor or against them.
- **Possible duplicate** — check company + title against existing records before adding a new one.
- **No connector available** — do the analysis and verdict in full anyway, and say clearly that nothing was saved, rather than staying silent about it.
