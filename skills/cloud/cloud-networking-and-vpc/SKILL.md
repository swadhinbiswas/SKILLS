---
name: cloud-networking-and-vpc
description: Design VPC/VNet networks that route correctly and cost what they should - CIDR and subnet planning, public vs private subnets, route tables including the main-table blackhole, NAT gateway and the single-NAT/SNAT trap, security groups vs NACLs, and private endpoints to avoid NAT charges. Use when laying out a new VPC or VNet, when a private subnet has no outbound internet, when someone asks why a NAT gateway costs so much, or when a resource in one subnet references an endpoint in another.
compatibility: AWS VPC, Azure VNet, GCP VPC. The concepts are shared; the resource names and defaults differ. Verify current pricing and per-service quota/limits with each provider.
metadata:
  version: "1.0"
---

# Cloud Networking and VPC

A VPC is three things that must agree: **addresses** (CIDR plan), **routing**
(how a packet leaves a subnet), and **filtering** (what is allowed). Most
networking incidents are a disagreement between the three - a subnet with no
route, a route that points at the wrong gateway, or a firewall that allows
something the route table never delivers.

The cost surprise is almost always the same two things: **a NAT gateway
processing traffic it should not have**, and **cross-AZ / cross-region data
transfer**. Design those out from the start.

## Workflow

- [ ] 1. Plan CIDRs across the whole estate (org, region, environment) - do
      this before creating anything
- [ ] 2. Lay out subnets per AZ: public / private-app / private-data, sized for
      the IP count you will need (VPCs are not elastic)
- [ ] 3. Decide the egress story: which subnets get a NAT, how many, and where
- [ ] 4. Decide the cloud-internal egress story: private endpoints / private
      DNS, so you do not send AWS/GCP traffic out through a NAT
- [ ] 5. Filtering: security groups as the primary control; NACLs only for
      subnet-level or a coarse guardrail, never as the main mechanism
- [ ] 6. Write it down (a CIDR table) and enforce non-overlap - overlapping
      CIDRs are painful to undo (you cannot renumber a live VPC)

## The CIDR plan, before the first subnet

- **Non-overlapping is the hard requirement.** Overlapping VPC CIDRs cannot be
  fixed by editing a live network - you would have to rebuild or use a
  transit/peering workaround. Reuse a plan across accounts? Peered/transit
  networks must not overlap, so the plan has to be global, not per-VPC.
- **VPC size is fixed at creation and is hard to change.** Allocate for the
  whole region/environment, not for today's instance count. Subnet CIDRs are
  fixed too (you can add CIDRs to subnets within a VPC's range, and add
  secondary CIDR blocks to a VPC in some providers - check current limits, the
  per-VPC secondary-CIDR count is a quota).
- **IPv4 is not the only plan.** Plan an **IPv6** range too if the provider
  supports dual-stack: many services require IPv6 allocation to give a
  resource an IPv6 address, and post-transition (AWS completed its
  IPv6-only migration in 2024) a VPC can require an IPv6 CIDR. Leaving IPv6
  out and adding it later means new subnets and route changes.
- **Size subnets for the IP count you will need, minus reservations.** A
  /24 has 256 addresses, of which AWS reserves 5 by default (verify the
  current reserved count - it has changed over the years). A subnet running
  ECS with ENIs per task burns IPs fast; a subnet running Kubernetes with a
  pod CIDR range needs headroom. Reserve at least ~20% spare in a subnet
  meant to grow.
- **Split by AZ, not by function, as the primary axis.** A subnet spans
  exactly one AZ. Subnet-per-AZ (with a public/private tier inside each AZ) is
  the default that survives AZ failure and keeps routing symmetric. This is
  the "subnet design" question with one right answer: **one public + one or
  more private subnets per AZ**, and data subnets separate from app subnets.

## Public vs private subnets, and the route tables

A subnet is "public" or "private" **only by its route table**, not by its
CIDR and not by an ACL.

| Subnet type | Route to internet | Route to on-prem | Hosts |
|---|---|---|---|
| **Public** | Yes - via an internet gateway (AWS), a load balancer with a public IP (AWS), or a public IP on the VM | Usually via the same or a separate gateway | Bastions, public load balancers, NAT gateways, anything with a public address |
| **Private (app)** | Only if its route table has an explicit NAT route | Transit gateway / VGW / peering | Application servers, containers, serverless with VPC attach |
| **Private (data)** | No route to the internet at all | Transit/VGW | Databases, caches, internal queues - nothing here should ever reach the internet directly |

**The main-route-table blackhole (AWS).** In AWS, the **main route table is
the default for every new subnet that has no explicit association**. If the
main route table has a route to an internet gateway, then **every new subnet
you create is public by default** - a classic and quiet security regression
that happens because someone added a `0.0.0.0/0 -> igw` route to the main
table "to fix something". The default-deny posture is: the **main route table
has NO internet route** (only the local route), and each subnet is explicitly
associated with a purpose-built route table. Verify: a subnet with a public
IP and no explicit route table inherits the main table; if that table has an
internet route, it is public. This is the single most important AWS
networking rule in this document. (Azure: a subnet has no implicit internet
access; outbound is explicit via a NAT gateway / load balancer / public IP
association. GCP: Private Google Access and Cloud NAT are explicit; a subnet
with no Cloud NAT route has no general internet egress.)

**Filtering is not routing.** A security group allowing `0.0.0.0/0` on port 443
does not give a host internet access - you still need a route. Conversely, a
route out does not let anything in - you still need a security group allowing
the return. People conflate the two constantly ("the SG allows it, so it
works" - no, if there is no route it times out; "it has a route, so it's
exposed" - no, if no SG allows the inbound you still cannot get in).

## Egress: the NAT gateway, the cost, and the SNAT trap

A private subnet needs outbound internet access for OS updates, package
repos, and external APIs. The mechanism is a **NAT gateway** (AWS), **Cloud
NAT** (GCP), or **NAT gateway on a subnet** (Azure) reached through a route
`0.0.0.0/0 -> nat-gateway`. Understand its cost and its failure modes:

- **Cost is per hour per gateway plus per GB processed**, and (on AWS) a NAT
  gateway is per-AZ. That is why "one NAT per AZ for availability" triples
  the hourly cost - a real, sometimes-correct, sometimes-not decision. A
  single NAT is a single point of failure; a NAT per AZ is highly available
  but costs 3x the hourly and complicates egress IPs. The compromise most
  teams land on: a NAT per AZ only where the workload is truly
  availability-critical, and accept the cost knowingly, or a NAT per region
  with the understanding that an AZ failure takes egress with it.
- **The SNAT trap (single NAT, cross-AZ)**: if every AZ's private subnet routes
  `0.0.0.0/0` to **one** NAT gateway in AZ-a, then *all* egress traffic from
  AZ-b and AZ-c **crosses into AZ-a first** and is billed as **cross-AZ data
  transfer** on top of the NAT per-GB charge. This is the "why did my NAT bill
  triple when I added a second AZ" moment. A NAT gateway per AZ keeps egress
  AZ-local and removes that cross-AZ charge; it costs more hourly but is often
  cheaper in total and is the recommended production shape.
- **Cross-AZ for anything, not just NAT**, is billed. A database client in one
  AZ talking to a service in another pays per GB. Keep a subnet's consumers and
  its dependencies in the same AZ where you can.
- **Egress IP stability matters for allowlists.** A NAT gateway has a fixed
  Elastic IP (AWS) / a reserved address (GCP); that address ends up in
  third-party allowlists. If you later move or replace the NAT, those
  allowlists break. Allocate and record the EIP deliberately.

### Do not route cloud-internal traffic through a NAT (the cost and design win)

Traffic *to the cloud provider's own APIs* (S3, DynamoDB, SQS, ECR, Secrets
Manager on AWS; Storage, Pub/Sub on GCP; Storage, Service Bus on Azure) should
travel over the provider's private path, **not** out through a NAT. Doing
otherwise means paying NAT per-GB **and** often cross-AZ for traffic that has a
free route.

| Provider | Mechanism | Notes |
|---|---|---|
| AWS | **VPC endpoints** (interface endpoints, Gateway endpoints for S3/DynamoDB) + **private DNS** | An interface endpoint puts an ENI in a subnet with S3-like private IPs; with private DNS enabled (or the right `ec2:ResolveVpcEndpoint` / `AssumeRoleForVpcEndpoint` policy) private service names resolve to the endpoint. Gateway endpoints are free, route-table based, and S3/DynamoDB only |
| GCP | **Private Google Access** (subnet flag, routes to Google's network) and **Private Service Connect** for published services | Set once per subnet; no per-service endpoint to create |
| Azure | **Private endpoints** + **private DNS zone** (a `privatelink` zone linked to the VNet) | The DNS zone is the part people forget; without it, the traffic still goes out the public endpoint |

**VPC endpoints have their own cost** (an interface endpoint per AZ, per hour,
plus per-GB data processing) - so the decision is: for a high-volume, always-on
consumer of a given service, an endpoint (or Private Google Access, which is
free) is usually cheaper than NAT per-GB plus cross-AZ. For a low-volume
consumer, NAT is fine. This is a per-service decision, not a blanket one.
(Gateway endpoints for S3 on AWS cost nothing beyond the gateway itself and
save NAT per-GB; DynamoDB gateway endpoints likewise - verify current
endpoints that are still supported as "gateway" type vs interface.)

## Security groups vs network ACLs

- **Security groups / NSGs (Azure) are stateful, per-resource (or per-NIC),
  and the primary control.** Return traffic is automatic. Use them for
  everything meaningful.
- **Network ACLs (AWS) are stateless, per-subnet, and evaluated in number
  order (lower number first), with an implicit deny at the end.** Every
  inbound allowance must have a matching outbound return rule for the ephemeral
  port, or replies are dropped - the classic "I allowed TCP 443 inbound and
  responses never come back" NACL bug. This is why NACLs are a poor primary
  control.
- **Use NACLs for**: a coarse subnet-level guardrail (block a whole range, force
  logs), or a quarantine subnet, or an IP-blocklist at the edge. Not for
  application rules.
- **Prefer security group references over CIDR.** AWS security groups can
  reference other security groups (`source_security_group_id`) and other
  resources, so the database SG can allow exactly the app SG - not a CIDR,
  and not a broad range. Azure NSGs can be associated with a NIC/subnet and
  support service tags (`VirtualNetwork`, `AzureLoadBalancer`, `Internet`).
  Use that over enumerating CIDRs.
- **Be careful with "allow 0.0.0.0/0" and service tags.** `0.0.0.0/0` on a
  management port (SSH, RDP) to the internet is the single most common
  misconfiguration; if you must, scope it to your office/VPN CIDR or a
  bastion, never open it to the world.
- **NACL rule numbers and evaluation order** matter (AWS): the lowest-numbered
  matching rule wins, deny rules should be numbered to be evaluated *before*
  the allows they should override.
- **Egress filtering too.** A "no outbound" rule on a data subnet is a real
  control and stops exfiltration; enable CloudTrail/VPC flow logs and read them,
  or the rule is aspirational.

## The circular-reference trap (a resource referencing an endpoint in another subnet)

This is a specific, common, head-scratching failure when you put a VPC endpoint
or a private service in a different subnet from the resource that needs it.

- **The cycle**: to create an **interface VPC endpoint** in subnet "endpoints",
  the endpoint's ENIs must land in a subnet with **free IP addresses**. If
  that subnet is managed by the same Terraform state and its IP capacity (or a
  count of ENIs) depends on a value that in turn depends on the endpoint (or on
  the resource that uses the endpoint), you have a dependency cycle, and
  Terraform reports:

  ```
  Error: Cycle: module.endpoints.aws_vpc_endpoint.s3,
  module.network.aws_subnet.endpoints: each depends on each
  ```

  The same shape appears with Cloud NAT (needs a router + an address), a private
  DNS zone that a resource's config depends on, or a security group that
  references another security group that references back.

- **Why it happens**: you put the endpoints in a dedicated subnet sized to
  hold the endpoint ENIs, and the number of AZs/IPs in that subnet is computed
  from the number of endpoints, which is computed from the resources that need
  endpoints.

- **How to break it** (in order of preference):
  1. **Decouple the subnet's IP budget from the number of endpoints.** Size
     the endpoints subnet for the *maximum* number of endpoints you will ever
     have and set the subnet CIDR statically. Then the subnet does not depend
     on the endpoints, and the cycle disappears. This is the correct fix.
  2. **Break the dependency explicitly**: give the endpoint (or the subnet)
     a value that does not require the other - e.g. size the endpoints subnet
     from a variable, not from `length(var.endpoints)`.
  3. Only as a last resort, `depends_on` to sequence the two - but
     `depends_on` on a resource that the subnet's IP capacity depends on does
     not help if the *value* is the cycle; it only helps when the cycle is
     Terraform's graph inference being conservative.
  4. If the endpoint is for a service with a **gateway** endpoint (S3,
     DynamoDB on AWS) there is no ENI and no subnet dependency at all - use
     the gateway type and the whole class of problem goes away.

- **Runtime version of the same trap**: a service in subnet A that is
  configured with the private DNS name of an endpoint that lives in subnet B -
  at plan time this is fine, but if subnet B has no route to/from where
  PrivateLink DNS resolves, the connection times out. "Resolves to a private
  IP" is necessary, not sufficient; the route and the security group still
  have to permit it.

- **Related failure to recognise**: "the resource can reach the internet but
  cannot reach the private endpoint" is almost always a **missing route or a
  security group** in the endpoint's subnet, not a DNS problem. Check the flow
  logs in the endpoint subnet.

## The other routing gotchas

- **Route tables have an implicit local route**; you cannot delete or override
  it, and it is why a route table on a peered VPC is not a full routing
  solution.
- **A more-specific route always wins** over a less-specific one. `10.0.1.0/24
  -> peering` beats `0.0.0.0/0 -> nat`, so adding a specific route is how you
  "fix" a subnet going out to the internet when it should go to an on-prem
  network. The blackhole technique (send a prefix to a `blackhole` route) to
  prevent a subnet from reaching the internet: a *more specific* blackhole
  route (e.g. `0.0.0.0/1` and `128.0.0.0/1` to `blackhole`, plus a normal
  default to the NAT) overrides the less-specific default. This is the
  "private subnet that must not reach the internet" pattern.
- **Security groups are evaluated per-direction and, within a direction, the
  union of rules allows.** Two SGs each allowing half the ports do not
  combine into one permissive SG for traffic between them; for a flow you need
  a rule on each side.
- **A security group on an ENI in a public subnet does not make the subnet
  private, and a route to a NAT in a public subnet does not make it public.**
  Publicness is the internet-gateway route, full stop.
- **IPv6 is not "security by obscurity".** If you allocate IPv6, you must
  filter it with the same rigour as IPv4; a default route to the internet for
  IPv6 in a "private" subnet hands out globally routable addresses that the
  NACL/SG must now also police. Either filter IPv6 egress or route it via the
  same NAT/egress you use for IPv4.
- **Peering/VGW/TGW do not transit.** Two VPCs peered to a transit gateway: A
  can talk to the TGW and B can talk to the TGW, but **A cannot talk to B**
  unless there is also a peering or a TGW route attachment between them. This
  is the "they are both on the transit gateway, why can't they reach each
  other" surprise.
- **A public load balancer needs public subnets; an internal one does not.**
  Putting an NLB/ALB in a private subnet is fine (internal ALB) and is the
  default for a service that should not be internet-facing.
- **Flow logs / VNet flow logs are how you debug "it just times out"** - turn
  them on and read them; a dropped packet between two correct-looking pieces
  of config is a routing or a stateful-firewall-return problem, and the flow
  log names which.

## Gotchas

- **The main route table defaulting every new subnet to public (AWS)** is the
  most consequential default in cloud networking. Audit it: no internet route
  in the main table, explicit route tables per subnet.
- **NAT per-AZ triples the hourly cost but removes cross-AZ egress charges.**
  For a production multi-AZ VPC the per-AZ NAT is usually the better total
  cost; a single NAT is cheaper in idle hours and more expensive in busy
  multi-AZ egress. Decide with numbers, not principle.
- **Interface VPC endpoints are per-AZ and per-hour.** A dozen endpoints across
  three AZs is a dozen-plus hourly line items - do not create an endpoint for
  every service "just in case"; create them for the high-volume services
  (S3, DynamoDB, ECR, CloudWatch Logs for a busy environment) and use NAT for
  the long tail.
- **Gateway endpoints (S3/DynamoDB, AWS) are free and route-based** - if you
  see a per-GB NAT bill dominated by S3 traffic, you are missing a gateway
  endpoint. This is one of the highest-value, lowest-effort network changes.
- **Private Google Access / Private DNS must be enabled, not just the
  endpoint.** An interface endpoint without private DNS (or a route) leaves
  traffic going out the public path; the endpoint is just there.
- **An NACL needs an outbound return rule** (statefulness) - a single inbound
  allow is silently broken for replies.
- **A security group that references another security group creates a real
  ordering dependency** and, if the two reference each other, a cycle. Model
  references in one direction (e.g. app SG -> db SG, never both).
- **A resource in a public subnet with an Elastic IP is reachable from the
  internet *only if its security group allows it*; a resource in a private
  subnet with a route to a NAT is not reachable from the internet at all
  (NAT is outbound-only) unless something (a bastion, a port forwarder, a VPN)
  is deliberately allowing inbound.** Getting these two backwards is the most
  common mental error.
- **Overlapping CIDRs cannot be fixed in place.** Plan globally. If two
  environments must share a CIDR, they need a peering/transit design that
  tolerates it (or IPv6), not a subnet edit.
- **Reserved IPs per subnet** (AWS reserves some by default - verify the current
  number) mean a /28 has fewer usable addresses than 16.
- **Flow/vnet logs have a cost and a lag.** They are the debugging tool of
  last resort and first resort respectively; enable them where packets are
  being dropped, not everywhere.
- **Transient DNS and health-check behaviour differs by service**: a load
  balancer health check comes from the LB's own network (an internal LB
  "sees" the source as the LB), and cross-zone settings change which AZ's
  targets a client can reach. When a service is "up but unreachable from one
  AZ", suspect cross-zone load balancing settings, not the app.

## Safety notes

- Never open SSH/RDP (or a database port) to `0.0.0.0/0` as a debugging step
  on anything reachable from the internet. Use a bastion/SSM/VPN, or a
  security group referencing a known CIDR.
- Network changes (route table edits, NACL changes, endpoint DNS) can cut
  running traffic in half a second and are hard to roll back cleanly. Apply
  them in a window, to a non-production VPC first, and know the rollback
  command.
- Do not resize or renumber subnets in a live VPC to "fix" an overlap - plan
  around it. Rebuild is the honest answer.
- When adding a NAT or endpoint, tag it and set a budget - per-AZ NATs and
  per-endpoint hourly costs add up quietly.
- Keep the CIDR plan and the flow of data (which AZ talks to which) in a doc
  that outlives the engineer. Most of these incidents are solved by the
  diagram nobody drew.
