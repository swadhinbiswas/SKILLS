---
name: cloud-cost-optimization
description: Find and reduce cloud spend with evidence - cost anomaly detection, rightsizing from real utilisation data, commitment discounts and when they actually pay off, storage lifecycle to cheap tiers, and the idle resources and data-transfer lines that dominate real bills. Use when a bill jumped, when someone asks how to cut costs, when choosing reserved/savings/scheduled pricing, when reviewing a Terraform change that adds always-on cost, or when setting up budgets and tagging.
compatibility: All three major clouds. Cost tooling names and commitment discount details change frequently; verify current SKU names, rates, and regional availability with each provider's pricing calculator and cost-management console.
metadata:
  version: "1.0"
---

# Cloud Cost Optimization

Cost work is data work. The wins come from **measurement** (what are we
actually paying for, attributed to an owner), **rightsizing against real
utilisation** (not guesswork), and **removing things nothing uses** (idle
load balancers, unattached volumes, forgotten NAT gateways, snapshots).

The most expensive line on a typical bill is not compute. It is, in order:
**data transfer out, idle/orphaned infrastructure, over-provisioned
databases and storage, and commitment discounts you did not take.**

## Workflow

- [ ] 1. Turn on cost attribution before anything else: tags, budgets,
      anomaly detection, and a per-service/per-owner breakdown
- [ ] 2. Attribute: every resource tagged with owner + env + app; untagged spend
      is a finding
- [ ] 3. Find the top 10 line items by cost, not by service count
- [ ] 4. For each: rightsize from metrics, move storage to a cheaper tier, buy a
      commitment, or delete it
- [ ] 5. For steady-state compute, evaluate commitment discounts against the
      12-month baseline
- [ ] 6. Re-measure next cycle; add a budget alarm so a regression is caught by
      a budget, not a shock

## Attribution first (do this before optimising anything)

- **Mandatory tags**: `owner` (a team or a person, resolvable), `env`
  (prod/staging/dev), `app`, `cost-center`. Enforce with a policy/tag rule
  (AWS tag policies / SCP on required tags, Azure Policy "modify" effect to
  auto-append, GCP org policy + labels) so a missing tag is impossible, not
  just discouraged.
- **Budgets and alerts** per service, per account, per team, and an overall
  one. Set a threshold at something like 50% of expected monthly spend with a
  notification, and a hard 100% one. A budget alarm is the cheapest insurance
  against a runaway resource, and it must be wired to something a human reads.
- **Cost anomaly detection / monitoring** (AWS Cost Anomaly Detection, Azure
  Cost Management budgets + cost alerts, GCP billing budget alerts) watch for
  the "one day, one region, 40x" event that a monthly review is too slow to
  catch. Enable it even if you think you would notice.
- **A daily cost report by tag** beats a monthly one. The point of attribution
  is to be able to ask "who added this" the same day.
- **Untagged spend is a reportable number.** If more than a few percent of
  spend is unattributed, the tagging work is incomplete and the optimisation
  work will stall at "whose is this?".

## Rightsizing from real utilisation data

Rightsizing without metrics is guessing. The tools that give you real data:
CloudWatch (AWS), Azure Monitor, GCP Cloud Monitoring - and the cost-side
"recommendations" built on them (AWS Compute Optimizer, Azure Advisor, GCP
Recommender). Prefer the source metrics; treat the recommendations as a
shortlist.

**The metrics that actually indicate over-provisioning:**

| Resource | Metric to read | What "too big" looks like |
|---|---|---|
| EC2 / VM | CPU **and** memory, p95 and average, over 30+ days | Average CPU well under ~10-15% *and* memory low. Low CPU alone is not enough (bursty workloads); memory is the one people forget |
| RDS / Cloud SQL / Azure SQL | `DatabaseConnections`, CPU, IOPS, storage growth | Very few connections and low CPU for a large instance class; a gp2/xlarge serving 3 queries |
| Load balancer | `ActiveConnections`, `NewConnectionCount`, request count | Zero or near-zero connections for days (an idle ELB is still billed) |
| Kubernetes nodes | Requests vs usage, pod density | Nodes at <30% requested capacity; consolidation into fewer/bigger nodes saves the per-node overhead (kubelet, CNI, OS) |
| Serverless | p50 vs p99 memory, duration, invocation count | Memory set from a guess; Lambda/Cloud Run is CPU-proportional to memory, so right-sizing memory is the lever |
| Storage volume | IOPS, throughput, size | gp2/gp3/io1 volume with a few IOPS; gp3 is usually the default answer and almost always better than gp2 |
| Queue / stream | backlog depth, oldest-message age | A queue with a backlog of 0 and a consumer that has been "scaling for peak" for a year |

- **Rightsizing is a two-sided change**: downsizing AND deleting. The
  over-provisioned resource that is not deleted is pure cost.
- **A downsized instance is a change with a real risk** (a load spike that used
  to fit no longer does). Do it to a stopped-but-preservable state where
  possible, keep snapshots, and pair it with a rightsizing policy so it does
  not immediately grow back.
- **Schedule non-production off.** A dev environment running 24/7 is often
  20-30% of non-prod spend and is the easiest win in the whole estate. Use
  instance schedules (AWS Instance Scheduler, GCP instance scheduling, Azure
  scheduled deallocate - note Azure deallocate keeps the disks billed; stop
  deallocates, which is the one you want).

## Commitment discounts: what they are and when they pay

Commitments trade flexibility for a discount in exchange for a **term** (1 or
3 years) and a **scope** (region, service family, instance family/size). The
discount magnitude is always on the provider's pricing page and varies by
region, service, and term - do not quote a number from memory, and do not
promise a percentage to anyone; quote "the current published discount for this
family/term/region" and show the calculation.

| Provider | Instrument | Covers | Use for |
|---|---|---|---|
| AWS | Savings Plans (Compute / Machine Learning / Database) | Compute (EC2, Fargate, Lambda) regardless of instance family/region within scope; a DB Savings Plan type exists | Steady, non-spiky compute where you want flexibility across families |
| AWS | Reserved Instances | One specific family/region/tenancy, convertible between sizes of the same family | You know the exact instance type and it is stable |
| GCP | Committed Use Discounts (CUD) for N1/N2/C2D, memory-optimized, GPU | Specific machine families in a region; a **resource-based** CUD ties to one VM | Stable base of specific machine types |
| GCP | Flex CUDs | A broader scope across families for a shorter term (1 year) | Steady compute you want some flexibility in |
| Azure | Reservations (VM, SQL, App Service) per region, 1 or 3 years | Specific region + specific service/instance | A known, stable Azure footprint |
| All | Spot / preemptible (interruptible) | Deep discount, **can be reclaimed with ~30s notice** | Batch, fault-tolerant, queue-backed work only |

**When they pay off, concretely:**

- Compute cost is a meaningful, *stable* share of the bill **and** you have at
  least ~a full billing cycle of data showing the baseline. Commit against the
  *stable floor* (the p10 of usage, not the average and not the peak) - commit
  above your floor and you are paying for idle capacity at a discount rate.
- A commitment covers **usage that actually runs in that region/scope**.
  Purchase against a baseline measured in the same scope you are committing
  in, or the discount does not apply and the bill does not move.
- The provider's own recommendation ("you could save X by committing Y hours")
  is a useful starting point and also **optimistic** - treat it as a
  hypothesis to validate against your own 30-90 day history.
- **Run the numbers before you commit**: `(on-demand hourly rate x hours) x
  (1 - published discount) x hours-committed` versus the on-demand cost for the
  same hours, for 1-year and 3-year. A commitment on a workload that then gets
  deleted leaves you paying for hours you do not use (savings plans are
  discounted but still charged; reservations are the most rigid).
- **Combine with the flexibility instrument, not just the cheap one**: a
  Savings Plan / Flex CUD for the stable base plus on-demand for the peaks is
  usually better than one deep reservation for everything.

**Do not commit to:** anything still being right-sized (you may delete it),
anything seasonal you cannot forecast, anything you might migrate off, and
anything whose business case depends on a product you plan to replace. If the
team is still choosing between two architectures, do not buy a 3-year
commitment for either.

## Storage: move cold data down the tiers

Storage is usually cheap per GB and expensive in aggregate because of
accumulation and because nothing ever gets deleted. Three moves, in order of
impact:

1. **Delete what nobody owns.** Old snapshots, orphaned volumes, abandoned
   AMIs/images, "temporary" buckets from 2019. Storage is not expensive per
   unit; it is expensive because it is infinite and forgotten. Tag it with an
   owner and an expiry; run a scheduled report of untagged/old storage.
2. **Lifecycle to cheaper tiers.** A storage class table (approximate, always
   confirm current classes and their minimum durations and retrieval costs for
   your provider):

   | Tier | For | Watch out for |
   |---|---|---|
   | Standard / Hot | Frequent access, active data | - |
   | Infrequent Access | Monthly-ish access | A **minimum storage duration** and a **per-GB retrieval charge** - reading more than you expect makes it more expensive than Standard |
   | Archive / Coldline / Deep Archive | Rare access, backups, compliance | **Retrieval takes hours (not minutes)** for the deepest tier; **minimum retention** charges if you delete early; a small per-GB read cost on retrieval |

   Lifecycle policies automate the move, so the rule is: set a lifecycle policy
   on every bucket/container/volume, transitioning objects older than N days to
   a colder tier, and **expiring** anything past a retention window. The
   transition windows should come from the actual access pattern (hot for
   30 days, then IA, then archive past 90, for example) - not from a guess.
3. **Deduplicate and compress** before you optimise the tier - a tier rule on
   data that was 5x larger than it needed to be just moves the waste.

**Gotcha that costs real money:** moving data *into* archive, then reading it
back before the minimum duration, incurs both a retrieval charge and possibly
an early-deletion charge. Archiving a database dump you re-read monthly is a
net loss. Match the tier to the *actual* read frequency.

## Idle and orphaned infrastructure: the "forgot it was there" money

These are billed whether used or not, are extremely common, and are free to
delete:

| Resource | Billed when idle | Detect with |
|---|---|---|
| **Load balancer / NAT gateway / gateway** | Per hour, always | Zero connections/bytes for 7+ days. NAT gateways are the single most common surprise - one unused NAT in a subnet nobody routes to |
| **Unattached EBS disk / managed volume** | Per GB-month | Volumes with no attachment |
| **Snapshot** | Per GB-month, and they accumulate | Snapshots older than any stated retention, not attached to an AMI in use |
| **Elastic IP (unassociated)** | Small hourly charge when unassociated | Unassociated addresses |
| **Elastic IP attached but idle** | Data transfer only, but a fixed charge; check the address's traffic | - |
| **Idle database instance** | Per hour | A non-prod DB running 24/7 (schedule it) |
| **Old AMI / container image / build cache** | Storage | Unreferenced images, ECR/GAR/ACR old tags, build caches |
| **Public IPv4 addresses** | AWS began charging for public IPv4 per address per hour - a broad idle charge | Count of ENIs/VMs with public IPs you did not intend |

- A **NAT gateway** is the highest-value item on this list: it is per-hour
  **and** per-GB, and VPCs accumulate them (one per AZ "for availability",
  one per "temporary need", one per legacy stack) and nobody removes them. The
  fix is both deletion and architecture (VPC endpoints / private Google
  access for cloud-internal traffic so you do not need NAT for it - see
  `cloud-networking-and-vpc`).

## Data transfer: the most under-estimated line item

Data transfer is billed by direction and often by source and destination, and
it is where a well-run estate still gets surprised.

- **Transfer *out* of the cloud to the internet is billed** per GB and is the
  single most common "why is the bill up" cause. It is also the easiest to
  misattribute - the bill attributes egress to the *resource that sent it*, so
  an untagged NAT gateway or a runaway log shipper shows up as egress cost.
- **Cross-AZ traffic inside a region is billed** on many services. Architect
  for AZ-locality; a Multi-AZ database, a cross-AZ NAT, or a client in one AZ
  talking to a compute instance in another pay per GB.
- **Cross-region transfer is expensive** and usually a mistake. Keeping a
  primary in one region and a read replica or a log/analytics copy in another
  is billed both ways; know it is there.
- **Cloud-internal traffic should be free** (via private endpoints / private
  link / VPC-native access). If you are paying to send S3 or Pub/Sub or
  BigQuery traffic *out through a NAT gateway*, you are paying egress for
  traffic that has a free private path - that is a design bug worth more than
  most discounts.
- **Logs and telemetry are the usual egress volume.** Shipping verbose logs
  cross-region or cross-account "for search" is a large, steady, easy-to-miss
  line. Set log retention, drop debug, and keep hot logs near their source.
- **Same-region, same-AZ traffic between a managed service and your compute**
  is generally free; the moment one of them is across a region or a NAT, it is
  not. Check the provider's data transfer pricing page for the specific pair.

## Prevent the bill shock

- **A budget alert is not a bill shock - if it is wired to something read.** An
  email to a distribution list nobody reads is a false control. Route anomalies
  to the on-call or the owning team, and set a threshold below the amount that
  hurts (50% of expected, not 100% of the cap).
- **Test and preview environments are the top source of runaway cost**: they
  scale with CI load, run on timers that got turned off, and are not tagged as
  spendable. Cap them, schedule them, and give them their own budget that
  pages someone when it is exceeded.
- **Tag-based cost allocation and per-team budgets** turn "the bill is high"
  into "team X is high", which is an actionable conversation. Do this before
  optimising, not after.
- **Prefer the provider's cost-anomaly detection** for the "one bad day"
  detection; a monthly review will miss a 48-hour runaway and only report the
  total.
- **A cost regression belongs in CI** for infrastructure changes: a PR that
  adds an always-on resource, widens a NAT, or moves a tier should be visible
  in the plan review, with a rough monthly delta noted (see
  `terraform-plan-and-apply` for reviewing the change itself).

## Checklist before declaring a saving done

- [ ] The baseline was measured (30+ days) before the change, not assumed.
- [ ] The change is reflected in the *next* full billing cycle's bill, not just
      the provider's "estimated savings" widget.
- [ ] The workload still meets its latency/SLO after rightsizing (check the
      percentiles, not the average).
- [ ] Idle resources were **deleted**, not just stopped, where the provider
      bills for stopped-but-allocated resources.
- [ ] Lifecycle/tiering rules are in IaC and applied to *new* resources too, not
      just patched onto existing ones.
- [ ] Tags and budgets cover the new state, so the saving does not silently
      regress.

## Gotchas

- **Provider savings estimates are optimistic** and frequently assume you
  already know your baseline. Validate against your own history before
  committing to a term.
- **Reserved instances / commitments are still charged if you delete the
  instance** (you pay for the committed hours whether or not they are used, at
  the discounted rate). Deleting a committed workload mid-term does not refund
  it. This is the main financial risk of over-committing.
- **Rightsizing too aggressively in one step causes an outage** and gets the
  whole cost initiative discredited. Downsize in steps, keep a rollback, and
  start on non-production.
- **"Stopped" is not always "not billed."** A stopped instance is not billed
  for compute but its **volumes are**, and an allocated-but-stopped managed
  disk still bills. Azure **deallocate** keeps disks billed; **stop** does not.
  Verify per provider and per resource.
- **Data transfer into the cloud is often free; out is not.** A backup or
  ingestion pipeline can be free while the restore/egress path is expensive -
  test the *read-back* cost, which is the one that bites in an incident.
- **The cheapest tier has a minimum duration and a retrieval charge.** Moving
  data you actually read back out is a net loss; compute the effective cost,
  not the headline price.
- **A single "anomaly" alert is noise unless it routes to a human** with the
  service and region attached to the message.
- **Cost attribution by tag fails for shared/global services** (NAT gateways,
  egress, the registry) - allocate those by a stated rule (by owner of the
  consuming resource, or by a documented split) rather than pretending they
  are directly attributable.
- **Egress is attributed to the sender**, so a misbehaving service can look like
  "networking costs" in the bill. When networking spend spikes, look at what is
  sending bytes before looking at the network resources.
- **Reserved/savings plans purchased at a broad scope can cover less than you
  think** if the usage is in a different region or a different service family -
  the discount is scoped, and usage outside it is billed on-demand. Check the
  plan's coverage against your actual usage by region/family.

## Safety notes

- Do not delete a resource you have not positively identified as idle and
  unowned. A snapshot or a volume "not attached" may be the only copy of
  something. Confirm with the owner (from tags) and check the resource's own
  retention policy before deleting.
- Do not purchase a multi-year commitment on anyone's behalf. Present the
  calculation (baseline, hours, published discount, term, and the downside if
  the workload is deleted) and let a human sign off - the cost is real and the
  downside is asymmetric.
- Rightsizing production compute is an availability risk. Schedule it, keep a
  rollback, and never batch it with an unrelated change.
- Set budgets **before** the next billing cycle, and make the alert reach a
  human. A budget that fires after the money is spent is documentation, not
  a control.
