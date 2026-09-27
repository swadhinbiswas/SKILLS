---
name: production-incident-response
description: Triage and run a live production incident - establish impact and blast radius, mitigate before diagnosing (rollback beats root cause), communicate on a schedule, preserve evidence, then find the root cause afterwards. Use when something is down, erroring, slow, or leaking in production right now, or when a user says 500s, an outage, a broken deploy, or "users are affected". Triggers on "production is down", "outage", "incident", "sev1", "roll back", "5xx", "users are affected", "error spike", "paging", "postmortem", "on-call".
compatibility: Assumes you can reach logs, metrics, and deployment tooling for the system. The timeline template is the deliverable a human keeps.
metadata:
  version: "1.0"
---

# Production Incident Response

Two rules govern everything else.

1. **Stop the bleeding before understanding.** A rollback takes two minutes and
   ends the outage. A root cause found at minute 40 helps nobody who was
   affected at minute 3. You can always investigate a stable system; you
   cannot investigate a burning one.
2. **You are not the incident commander unless you are the incident
   commander.** If there is an on-call rotation, hand off explicitly, by name,
   with the current state. Do not run two parallel investigations.

The output of this skill is a restored service, a written timeline, and a
follow-up list. Not a fix.

## Workflow

- [ ] 1. **Declare** — is this an incident? If you are debating, it is. Declare
      at a low severity and downgrade later; you cannot upgrade an undeclared
      outage.
- [ ] 2. **Establish impact** — who is affected, how badly, since when,
      changing or not
- [ ] 3. **Mitigate** — the fastest available way to stop the damage. Roll back
      first, disable the feature, shed load, fail over
- [ ] 4. **Communicate** — one channel, one update every 15-30 minutes, no
      speculation about cause
- [ ] 5. **Preserve evidence** — before you restart, redeploy, or clean up
- [ ] 6. **Diagnose** — only now. Use `systematic-debugging`
- [ ] 7. **Hand off and write up** — timeline, contributing factors, actions
      with owners and dates

## Step 1 — Declare, and get the right people

- Open the incident channel/ticket. State: what is broken, who is affected,
  since when, and current severity.
- Name an incident commander. If you are solo, say so explicitly and keep the
  role anyway — naming it keeps you from drifting into doing three things at
  once.
- Do **not** page a broad distribution for a localised problem, and do not
  run an incident solo for a service with real users.

### Severity framing

Pick a level, state it out loud, and write it down. The level determines how
much of the company gets interrupted, so be honest in both directions.

| Level | Meaning | Examples |
|---|---|---|
| **SEV1** | Widespread outage or data loss risk; the product is unusable for most users | All requests 5xx, database down, payments failing, unrecoverable data loss |
| **SEV2** | Major degradation with a workaround; a large fraction of users affected | One region down, a feature fully broken, severe latency increase, queue backed up |
| **SEV3** | Minor degradation or a small subset affected | One customer's integration failing, a single node unhealthy, an internal-only tool broken |
| **SEV4** | Cosmetic, or no user impact | Internal logging failure, a broken dashboard, a typo in a doc |

Escalate immediately if any of these are true, regardless of the count:
data loss or corruption, a security or privacy exposure, a financial
correction needed, or a growing (not stabilising) failure count.

## Step 2 — Establish impact and blast radius

Answer these in order, from data rather than from a colleague's feeling:

- **What is broken?** The user-visible symptom, in one sentence. Not "the
  service" — "checkout returns 500 for logged-in users".
- **Since when?** First timestamp of the symptom, and the last known-good
  timestamp. This bounds the suspect window and usually points at a deploy.
- **Who is affected?** Which users (all, one tenant, one country, one plan),
  which requests, how many (rate, not just yes/no).
- **Trend:** growing, plateaued, or recovering? This decides whether you have
  minutes or hours.
- **What is the workaround?** If there is one, say so in the first
  communication. It changes the severity more than anything else.

```sh
# error rate by service/endpoint, last 30m vs the hour before
# (concrete query depends on your metrics backend — do not invent endpoints)
```

Useful correlations, in the order they usually pay off: deploy/rollout
timeline, error rate by version or region, dependency latency, queue depth,
connection-pool saturation, and the error *message* distribution rather than
the total.

**Blast radius is about the change, not just the failure.** If the suspect is a
new deploy, ask: what does this change touch? A config change to a
connection string affects everything downstream. A feature flag at 1% affects
1% — and the other 99% is your control group.

## Step 3 — Mitigate first

Ranked by how fast they restore service, not by how satisfying they feel.

| Option | Restores in | Notes |
|---|---|---|
| **Rollback** | ~2 min | The default. Cancels a deploy, a config push, a migration, a feature flag. |
| Disable a feature flag / kill switch | seconds | Should exist before the incident. Its absence is an action item, not a reason to hesitate. |
| Scale up / fail over to another region | minutes | Only if the bottleneck is capacity and the other region is healthy. |
| Shed load: disable the slow path, rate-limit, return cached or stale data | seconds | Degraded is better than down. Say what is degraded. |
| Restart the bad process / evict the bad node | ~1 min | Restarts destroy evidence. Capture what you need *first*. |
| Roll **forward** with a known fix | minutes | Only if you have a tested fix and the rollback is known-bad (e.g. a data migration already applied). |
| Fix forward live (hotfix, edit in place) | tens of min | Last resort. Very high risk of making it worse. |

### Rollback, precisely

```sh
# Kubernetes: previous ReplicaSet or revision
kubectl rollout undo deployment/<name> -n <ns>
kubectl rollout history deployment/<name> -n <ns>
kubectl rollout status  deployment/<name> -n <ns>   # watch it actually roll

# Platform-agnostic, if your deploy system tracks revisions
<deploy-tool> rollback --to <previous-revision>
```

Verify the rollback worked before saying anything is resolved: check the error
rate is back to baseline, not that the deploy command returned 0.

**Rollback is not always possible.** Schema migrations, deleted data, and
irreversible queue messages cannot be rolled back — only rolled forward.
That asymmetry is why expand-contract migrations matter (see
`postgres-query-tuning`).

If you must roll forward, you are now making a change to production during an
incident: get explicit approval, have someone else review it, and monitor
rather than deploy-and-walk-away.

## Step 4 — Communicate on a schedule

- **One channel, one source of truth.** Every update in one place. Side
  conversations produce three conflicting timelines.
- **Update every 15-30 minutes even if the update is "no change".** Silence
  is read as "nobody is working on it".
- **Say what you know, what you don't, and what you are doing next.** Never
  speculate about root cause before mitigation; a retracted cause is worse than
  no cause.
- **Do not page people with unconfirmed hypotheses.**

Update template:

```
[HH:MM UTC] SEV<n> — <one-line status: Investigating / Mitigating / Monitoring / Resolved>
Impact:    <who, what, roughly how many, since when>
Mitigation:<what was done, or what is being tried now>
Status:    <what changed since the last update; "no change" is a valid update>
Next:      <the next action and when we will report again>
Owner:     <name>
```

Escalate when: the mitigation did not work, the impact is growing, you have
been at it for longer than the severity's target time-to-mitigate, or you need
authority you do not have (a paid feature down, a security issue, a customer
communication).

**Never, as a default action:** send customer emails, post to social media,
post in a public status page, contact customers directly, or change a billing
or security setting. Draft these and surface them for a human to send.

## Step 5 — Preserve evidence before you clean up

Every mitigation destroys evidence. Capture first — it takes two minutes.

- Export or screenshot the dashboards *showing the failure*, before a restart
  smooths the graph.
- Capture the exact error messages and their distribution.
- Note the current versions: deployed commit SHA, image tag, config revision.
  **Write the version down** — after a rollback you will not remember it.
- `kubectl describe pod`, container logs, and the container's exit reason
  (OOMKilled, error, etc.).
- A heap/core dump or a memory snapshot if this is a memory incident
  (`memory-leak-hunting`).
- The offending request/trace IDs from affected users. Ask for one concrete
  failing request id early — it is the fastest route to the root cause.
- Any state you are about to destroy: pod logs on a node being drained, a
  queue's contents, a database's `pg_stat_activity`.

Snapshot before killing:

```sh
kubectl logs <pod> -n <ns> --previous > /tmp/pod-previous.log 2>&1
kubectl describe pod <pod> -n <ns> > /tmp/pod-describe.txt
kubectl get events -n <ns> --sort-by=.lastTimestamp | tail -50 > /tmp/events.txt
```

## Step 6 — Diagnose, only after it is stable

Once the service is restored or at a stable degraded state, switch to
`systematic-debugging` and work the queue properly. The order that pays off:

1. **What changed?** The single most likely cause of any production incident
   is a change. Diff the suspect window against the last known-good: deploys
   (including config, images, and *infrastructure* changes), dependency
   bumps, data changes, traffic changes, and dependency/3rd-party status.
2. **Bisect the change** with `git bisect run` if there are several candidates
   in the window.
3. **Was the change actually the cause?** A deploy that coincides with an
   incident is a suspect, not a verdict. A deploy 3 hours before the onset
   that only affects a cold path is probably innocent.
4. **Why did monitoring not catch it earlier?** The gap between detection and
   impact is a finding, not a footnote.

Do not restart the investigation from zero when a new alert fires — that is
the most common way an incident never closes.

## Step 7 — Hand off and write it up

- Confirm **monitoring has been restored** before you declare resolution. If
  the alert that paged you is still firing, the incident is not over.
- Hand off explicitly if a shift change occurs, mid-state, in writing.
- Write the postmortem while it is fresh. Blameless, specific, about systems
  and decisions — not about people.

### Timeline template

```markdown
# Incident: <title>
Severity:   SEV<n>
Started:    <YYYY-MM-DD HH:MM UTC>  (first observed)
Detected:   <HH:MM UTC>  (by what: alert / user / monitoring)
Mitigated:  <HH:MM UTC>  (what stopped the bleeding)
Resolved:   <HH:MM UTC>  (full service restored)
Duration:   <impact duration> impact / <total> to resolve

## Impact
<Who was affected, what they experienced, how many requests/users,
 approximate revenue or SLO burn, and whether it was silent.>

## Timeline
| Time (UTC) | Event | Actor |
|---|---|---|
| HH:MM | last known good | — |
| HH:MM | deploy <sha> started | CI |
| HH:MM | error rate rises to X% | monitoring |
| HH:MM | alert fires: <alert name> | PagerDuty |
| HH:MM | incident declared, IC = <name> | <name> |
| HH:MM | rollback to <sha> | <name> |
| HH:MM | error rate back to baseline | monitoring |
| HH:MM | monitoring restored, incident closed | <name> |

## Root cause
<What actually caused it, with the evidence. Not "the deploy".>

## Contributing factors
<What let a small change become a large outage: review gaps, missing tests,
 missing feature flag, alert that fired late, no runbook, capacity headroom.>

## What went well
<Be specific. This is what you keep doing.>

## Action items
| # | Action | Type | Owner | Due | Tracking |
|---|---|---|---|---|---|
| 1 | Add a kill switch for <feature> | prevent | | | |
| 2 | Alert on <leading indicator>, not <lagging one> | detect | | | |
| 3 | Expand-contract migration for <schema change> | prevent | | | |
| 4 | <runbook entry for this failure mode> | prepare | | | |

Action items need an owner and a date, or they are wishes. "Prevent" fixes
the cause, "detect" shortens the next one's discovery time, "mitigate" shortens
its impact — you need all three.
```

## Gotchas

- **Restarting feels like fixing and destroys the evidence.** Capture first.
  A crash-looping pod's `kubectl logs --previous` is gone after the restart.
- **The deploy that coincides with the incident is not automatically the
  cause.** Post-incident changes, capacity events, and dependency failures
  cluster in time. Confirm with a mechanism.
- **"It recovered on its own" is a worse outcome than a fast rollback.** It
  means the cause is still present and you do not know what it was. Do not
  close the incident because the graph went flat; close it because you
  mitigated.
- **A rollback that takes longer than expected is a new incident.** Watch
  `rollout status`, not the command's exit code.
- **Rate limits, retries, and circuit breakers are real mitigations**, not
  hacks. If the system has them, use them before improvising.
- **Do not debug by mutating production state.** Restarting pods, changing env
  vars, editing config, `kill -9`, or running ad-hoc queries that write are
  changes to a live system. Each one needs explicit human approval, a stated
  rollback, and an audit note.
- **Do not run the remediation of a data-corruption incident before you have
  a backup and a restore plan.** Making it worse is a one-line action.
- **Multi-region: check whether your "fix" is being served everywhere.**
  A partial rollout means half your traffic is still on the bad version.
- **Customer-visible latency without errors is an incident too** — and a
  harder one, because no alert may be configured for it. Check the SLO, not
  the error rate.
- **The person who pages is not the person who fixes.** Routing the page to
  the team that owns the dependency, not the team that noticed, is most of the
  value of an on-call rotation.
- **After a long incident, write the timeline before you go to sleep.** Memory
  of an incident degrades fast and the postmortem is the only durable record.

## When the mitigation itself is risky

Some incidents have no safe fast action: a data-corruption incident, a leaked
credential, a security breach. In those cases:

1. Prefer **containment over restoration**: disable the affected path, restrict
   access, revoke the credential. A system that is safely degraded beats one
   that is fast and wrong.
2. Involve the people with authority immediately — security, legal, the
   on-call for the affected system.
3. Preserve everything; do not clean up, do not rotate things "to be safe"
   without recording what you did.
4. Comms are legal's call, not yours. Draft; do not send.

## Files

No bundled files. The timeline template in this skill is the durable artefact;
adapt its headings to your incident process rather than starting from scratch.
