# Description and triggering craft

The description is the only part of a skill most agents ever see before deciding
to load it. This is the craft behind writing one that reliably fires.

## The retrieval model

At startup, the client loads `name` + `description` for every installed skill
into a catalogue. When a user sends a request, the client (or the model) matches
that request against the catalogue. If your description does not overlap the
user's words, **the skill never loads** — regardless of how good the body is.

So a description is written for two readers:

1. **The matcher** — needs literal overlap with user vocabulary.
2. **The agent, once loaded** — needs to know the exact boundary of when this
   skill applies and when a *different* skill does.

## The shape that works

```
[Concrete capability] — [artefacts/verbs users type]. Use when [triggering
situations]. Triggers on "[literal cue words]".
```

- **Capability first**, in the user's terms, not an abstract category.
- **Artefacts**: the strings, filenames, commands, and error codes a user would
  paste. `EXPLAIN (ANALYZE, BUFFERS)`, `docker-compose.yml`, `useEffect`,
  `502 Bad Gateway`, `SKILL.md`, `git bisect`.
- **Verbs**: slow, flaky, crashing, stuck, review, refactor, deploy, debug,
  migrate, why-is-X-wrong, make-X-faster.
- **Use when**: the *situations*, phrased as the user would state them.
- **Triggers on**: a literal keyword list, useful when the surface vocabulary is
  distinctive and you want the match to be unambiguous.

## Anti-triggering

Two skills that fire for the same request split attention and confuse routing.
For each new skill, ask: *what is the adjacent skill it should NOT fire for?* Put
an explicit boundary in the description when there is one:

- "…Use when the **server** is slow (client-side profiling, cache, backend
  work). For a slow **test suite** use `<other skill>`."

If the boundary is genuinely blurry, the skills are probably one skill or the
domain split is wrong.

## Deriving keywords

Mine the vocabulary from where the requests actually come from:

- Support tickets / issue titles for the actual phrasings users use.
- The error strings and log lines people paste.
- The command names and file types that show up in the task.
- The words a *newcomer to this specific system* would use, not the internals
  your team uses.

Then check each candidate word: would it also appear in a request that should
*not* load this skill? If yes, it is a noise word — cut it or qualify it.

## Length

Descriptions have a hard cap (1024 chars in this repo's validator) and soft caps
in some clients that truncate. Two rules:

- Put the **trigger cue early** so it survives truncation.
- Do not pad with background. Every word should either name an artefact/verb or
  describe a triggering situation.

If your description feels long, it is usually because the skill covers two jobs.
Split the skill before you shrink the description.

## When a skill does not trigger

Work down this list; it is almost always the description, then the name:

1. **No literal overlap** — rewrite with the user's words, add the artefact and
   error strings.
2. **Too generic** — the description says what the skill does but not *when*;
   add "Use when …".
3. **Adjacent skill wins** — another skill in the catalogue matches more
   strongly; disambiguate both descriptions.
4. **The name misleads** — the name is what a human scans; make it name the job,
   not the technology ("redis-cache-stampede" over "cache-notes").
5. **The body is great but the body never runs** — the description is the
   product; fix it there, not in the prose.

## Self-check before shipping

- [ ] Written from a realistic user request, not from the skill's own body
- [ ] Names at least 2 artefacts/commands/error strings a user would type
- [ ] Contains 2+ of the user's real verbs
- [ ] Has an explicit "Use when …" with situations, not just a category
- [ ] States a boundary if an adjacent skill could also match
- [ ] Under the length cap, trigger cue early enough to survive truncation
- [ ] Does not start with "This skill…"
