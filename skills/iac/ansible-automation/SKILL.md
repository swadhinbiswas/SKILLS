---
name: ansible-automation
description: Write Ansible playbooks and roles that are idempotent, checkable, and safe to re-run - modules over shell, check mode and --diff, handlers, vault handling, and Molecule for testing. Use when a user asks to configure servers or cloud instances with Ansible, when a playbook is not idempotent or re-runs make changes every time, when secrets need encrypting with ansible-vault, or when someone is reaching for Ansible where Terraform or a config manager is the better tool.
compatibility: Ansible core 2.15+ (collection `ansible.builtin`, FQCNs). Molecule 6+ with the `molecule` plugin. Vault 2.x format. Verify collection versions with `ansible-galaxy collection list`.
metadata:
  version: "1.0"
---

# Ansible Automation

Ansible is the right tool when the unit of automation is a **machine you
already have** and the desired state is "make this machine look like this",
applied repeatedly and cheaply. It is the wrong tool when you are creating
infrastructure (that is Terraform) or when the change is a one-off.

The whole discipline is **idempotence**: running the playbook twice in a row
must make zero changes the second time. Everything below serves that.

## Workflow

- [ ] 1. Decide if Ansible is even the right tool (see "When Ansible is wrong")
- [ ] 2. `ansible-lint` and `yamllint` in CI, before review
- [ ] 3. `ansible-playbook --syntax-check`, then `--check --diff` against a real
      inventory target - read what *would* change
- [ ] 4. Apply, then immediately re-run in `--check` mode: it must be clean
- [ ] 5. Ship the role + a molecule scenario; a role with no molecule scenario
      does not get reused safely

## The loop you always run

```sh
ansible-playbook site.yml --syntax-check
ansible-playbook site.yml --check --diff -vv
ansible-playbook site.yml
ansible-playbook site.yml --check --diff    # must report zero changes now
```

`--check --diff` is not optional ceremony: it shows you what a playbook *would*
do, and *what* (the before/after) rather than merely *that*. Run it against a
real target - check mode against a host without the module installed is
useless, since most modules need the target reachable with Python present. A
second `--check` run after apply that still reports changes means the playbook
is not idempotent: find the task reporting `changed` every time and fix it.

## Idempotence

### Prefer modules over shell

A module knows the resource's current state, reports `ok` or `changed`
honestly, and supports check mode. A shell task reports `changed` every time
because it did run a command.

```yaml
# Bad: always reports changed; check mode is a lie
- name: Install nginx
  ansible.builtin.shell: apt-get install -y nginx
  changed_when: false     # hiding the problem, not solving it

# Good
- name: Install nginx
  ansible.builtin.apt:
    name: nginx
    state: present
    update_cache: true
    cache_valid_time: 3600
```

Module choices worth defaulting to:

| Want | Use | Not |
|---|---|---|
| Package installed | `ansible.builtin.apt` / `dnf` / `apk` | `shell: apt-get` |
| Service running | `ansible.builtin.service` with `state: started`, `enabled: true` | `shell: systemctl start` |
| A file with specific content | `ansible.builtin.template` or `copy` | `shell: echo >` |
| Lines in a config file | `ansible.builtin.lineinfile` (with a `regexp` marker) | rewriting the whole file with `template` if one line differs |
| A user or group | `ansible.builtin.user` / `group` | `shell: useradd` |
| A file a human also edits | `ansible.builtin.blockinfile` (managed block, idempotent, leaves the rest alone) | `template` (the playbook will fight the admin) |
| Extract an archive | `ansible.builtin.unarchive` | `shell: tar` |
| Cron | `ansible.builtin.cron` | dropping a file into `/etc/cron.d` |
| Anything with no module | `command` + a real `changed_when` | `shell` |

`ansible.builtin.command` does not run through a shell, so no globbing,
pipes, or redirection - which is exactly why it is safer. Reach for `shell`
only when you truly need a pipeline, and then make it idempotent explicitly.

### Making shell idempotent

If a shell task is genuinely needed, it must decide whether it changed
something. Compare before/after yourself, and use `changed_when` to say so.

```yaml
- name: Ensure the sysctl is applied
  ansible.builtin.shell: |
    if [ "$(sysctl -n net.ipv4.ip_forward)" = "1" ]; then
      echo "already-set"
    else
      sysctl -w net.ipv4.ip_forward=1 && echo "changed"
    fi
  register: ipfwd
  changed_when: "'changed' in ipfwd.stdout"
```

Other idempotence devices:

- `creates` / `removes` for commands that must not run twice
  (`file: state=touch creates=/opt/app/.installed`).
- `changed_when` on `command` that only reads (`changed_when: false`) - fine for
  `getent`, `systemctl is-active`, `kubectl get`.
- `failed_when` for non-zero exits that are not failures
  (`grep` returning 1 for "not found").
- `until` with a `retries`/`delay` for eventual consistency instead of
  `sleep`.

### Template and file gotchas

- `template: validate: "/usr/sbin/nginx -t -c %s"` runs a real check before
  writing - catch a broken template without breaking the service.
- `notify` a handler to restart a service; never restart a service in a task
  just because a config changed. Handlers fire **once per play run** (not per
  notifying task) and only if a notifying task reported `changed`. They do not
  run in check mode, so a handler that a `--check` run depends on means the
  check run is not representative. They also run in *definition* order, not
  notification order.
- Templating a value the user might legitimately change by hand
  (`motd`, network config) makes the playbook fight the admin. Use
  `ansible.builtin.blockinfile` with a marker, or `lineinfile` with a
  `regexp`, for anything a human also edits.

One trap worth its own line: `no_log: true` on a `become` task can hide the
very error you need. Put `no_log` on the narrowest possible task.

## Structure: playbook, roles, collections

- **Playbook**: the orchestration. Inventory references, `become`, and a list
  of roles. Keep it thin.
- **Role**: the reusable unit. `tasks/main.yml`, `defaults/main.yml`,
  `handlers/main.yml`, `templates/`, `files/`, `meta/main.yml`.
  `meta/main.yml` is the dependency graph - it is enforced and is the right
  place to express ordering.
- **Collection**: the packaging unit for modules. Always use **FQCNs**
  (`ansible.builtin.apt`, `community.general.htpasswd`) - bare `apt` is
  ambiguous between `ansible.builtin.apt` and any collection that also has one.
- **Vault-encrypted** values live in the role or group_vars, never in
  `defaults/`.

A role skeleton that is actually reusable:

```
roles/nginx/
  defaults/main.yml      # every tunable, documented
  handlers/main.yml      # reload/restart, `listen:` where shared
  tasks/main.yml         # include sub-tasks
  templates/nginx.conf.j2
  files/                 # static files
  meta/main.yml          # dependencies
  molecule/default/      # a test that proves the role works
  README.md
```

`meta/main.yml`:

```yaml
---
galaxy_info:
  role_name: nginx
  author: platform
  description: Managed nginx front end
  license: MIT
  min_ansible_version: "2.15"
dependencies:
  - role: common
    tags: [common, always]
```

`tags: [always]` on a dependency is the standard way to ensure a base role
runs even when the play selects a specific tag.

## Variables and precedence

Precedence, highest wins: `extra vars` (`-e`) > task `vars` > block vars >
`include_vars` > role vars > group_vars/all > group_vars/<group> > host_vars >
play vars > role defaults > inventory vars > `extra vars` file (last).
Check the current `ansible-config dump` output for your version before relying
on the exact order.

- **Defaults are for tuning, not secrets.** Anything in `defaults/main.yml` is
  public, overridable, and shows up in docs.
- **Facts** (`ansible_facts`) are computed per host at run time. Use
  `gather_facts: false` for a play that only touches one thing, and remember
  that a `delegate_to: localhost` task still sees the *target's* facts unless
  you gather them.
- **Facts are cached** - `ansible.builtin.setup` results, gathered
  automatically. If a fact is wrong it is usually a **stale cache**, not a
  bug: `ansible host -m setup` to check, clear the `fact_caching` dir, or
  `gather_facts: true` in the play. A playbook that "disagrees with the
  server" is very often this.
- Register and use structured facts rather than shelling out:
  `ansible_facts.services`, `ansible_facts.packages`, `ansible_facts.os_family`.
- An undefined variable inside a `lookup` fails at runtime, so use the lookup's
  own `default=` (`lookup('vars', 'my_var', default=[])`) rather than a
  `| default([])` filter after it - the filter runs after the lookup has
  already errored.

## Vault

```sh
ansible-vault create group_vars/all/vault.yml
ansible-vault encrypt_string 'hunter2' --name 'db_password'   # one value, in-place
ansible-vault edit secrets.yml
ansible-vault view --decrypt prod.yml | less                 # never to a file in the repo
```

```yaml
# group_vars/all/vault.yml (encrypted)
vault_db_password: hunter2
vault_db_user: app
```

```yaml
- name: Set the database password
  ansible.builtin.set_fact:
    db_password: "{{ vault_db_password }}"
  no_log: true        # hides it from logs AND from -v output
```

**The vault gotcha: an encrypted value is not idempotent.** This is the one
people get wrong.

- A vaulted string is just a string. Templated into a config file, `template`
  compares content and will not rewrite an identical file - that part is
  idempotent, and it is fine.
- The failure is elsewhere: a secret **written through a command**
  (`userdel`/`useradd`, `htpasswd -B`, a token printed by
  `command: ... register: out`) runs every time, and the registered output is
  compared against a value you cannot see. People reach for
  `changed_when: false`, which is lying.
- The fix is to make the credential creation conditional on a *readable,
  comparable* check, not on the secret's value: derive `changed_when` from
  whether the file or entry actually changed, or compare a **hash** of the
  current state, or use the module's own idempotence (`htpasswd` writing a
  file with `state: present`).
- **Rotating secrets and Ansible do not mix well.** Vault is for "the same
  secret, delivered securely", not for rotation. Rotation is a secrets
  manager's job (AWS Secrets Manager, HashiCorp Vault, SOPS + age); Ansible
  just reads the current value.

Two more rules: encrypting a *whole file* hides its structure, while encrypting
individual values (`encrypt_string`) keeps review possible - prefer per-value in
a `vault.yml`. And `no_log: true` on **any** task that touches a secret,
otherwise the value appears in output and in `-v` runs.

## Check mode and other safety flags

- `--check` - do not make changes. Most modules support it; `command`/`shell`
  often do **not** and will report they are skipped.
- `--diff` - show what would change (file/template/line tasks).
- `--syntax-check` - parse only, no inventory contact. Fast pre-commit check.
- `--limit webservers` - target a subset; the standard canary move.
- `--list-hosts` / `--list-tasks` / `--list-tags` - understand the run without
  running it. `--step N` - interactively confirm each task; slow, but the right
  call the first time you run a destructive role in production.
- `serial: 20%` in a play rolls through a large fleet in batches with a pause
  between them; combine with `max_fail_percentage` to abort a bad run before it
  hits everything. `--limit` plus `serial` is the standard safe fleet rollout.

## Molecule: prove the role works

Molecule builds a throwaway instance, converges a role against it, verifies the
result, and tears it down. It is the only practical way to know a role is
idempotent — nothing in a normal play catches a task that changes something on
every run.

A scenario runs three phases:

```yaml
# molecule/default/converge.yml
---
- name: Converge
  hosts: all
  gather_facts: true
  roles:
    - role: nginx

# molecule/default/verify.yml - assert observable state, not "the play returned ok"
---
- name: Verify
  hosts: all
  tasks:
    - name: nginx config is valid
      ansible.builtin.command: nginx -t
      changed_when: false

    - name: collect service state
      ansible.builtin.service_facts:

    - name: assert nginx is running
      ansible.builtin.assert:
        that: ansible_facts.services['nginx.service'].state == 'running'
        fail_msg: nginx is not running
```

**Idempotence is the assertion that matters, and the one most roles fail.** Do
not read it off a registered `include_role` — the include itself always reports
`changed`, which makes such an assertion meaningless. The working check is to
re-converge in check mode against the already-converged instance and assert
nothing reports `changed`:

```sh
molecule test                 # converge, idempotence, side_effect, destroy
molecule converge -e debug=true
molecule idempotence
```

Read `references/molecule.md` when setting Molecule up for the first time, or
when idempotence is failing and you need the full driver/phase reference.

## When Ansible is the wrong tool

| Situation | Use instead | Why |
|---|---|---|
| Creating cloud resources, subnets, IAM, load balancers | Terraform / Pulumi | Ansible's cloud modules create one thing at a time, with no state, no plan, and no drift view. It works, but you have given up every safety property |
| Per-machine OS config, packages, users, files | Ansible | This is its job |
| One-off command on one box | Just run it | A role for a single `sed` is overhead |
| Anything needing a "what will change" review before it changes | Terraform plan, or Ansible `--check --diff` (weaker) | Ansible's check mode is not a plan: it is per-task and imperfect for anything without module support |
| Configuration with a templating and rollback story (Kubernetes manifests, nginx config variants) | Helm / a config-management tool | Jinja2 + `when:` chains get unreadable fast |
| Fleet at scale (hundreds of hosts, frequent runs) | Ansible with a tuned fact cache and `strategy: free`, or a config-management tool | Per-host SSH overhead dominates; consider `ansible -f 50` and connection multiplexing |
| A secret that must rotate | SOPS / a secrets manager | Vault does not rotate |

A useful test: **can you say what the diff is before it runs?** Terraform
answers with a plan. Ansible answers with `--check --diff`, which is closer but
coarser. If the change is expensive to reverse, reach for the tool that gives
you a real plan.

For the IaC testing that pairs with this (policy scans, Terratest, plan
assertions) see `infrastructure-testing`.

## Gotchas

- **`--check` is best-effort, not a simulation.** `command`/`shell` tasks are
  skipped; modules that must read from a live system may report `changed` or
  skip. A clean check run does not guarantee a clean apply, and vice versa.
- **A task that runs `command` and registers output is compared as a string.**
  A trailing newline or changed formatting reads as drift. Use
  `changed_when` explicitly for anything registered and compared.
- **Ansible is not transactional.** A play that fails at task 30 leaves tasks
  1-29 applied. Design roles so a partial run is resumable (which idempotence
  gives you) and make the failing task safe to re-run.
- **Facts are per-host and not shared**; a fact set on one host is invisible on
  another. Use `hostvars` to read across hosts, and `delegate_facts`/
  `delegate_to` with care. An undefined `hostvars` lookup fails at runtime, not
  parse time - use `hostvars['x'].y | default('fallback')` and then assert.
- **A loop with `when` on the item plus an item-level `failed_when` can skip
  the `failed_when` entirely** for filtered-out items. Assert inside the loop
  body if the condition matters.
- **Handlers always run at the end of the play, never immediately** - including
  when notified from inside a `block`. If a service must restart before a later
  task in the same play, use `meta: flush_handlers`.
- **`until` loops charge the full `retries * delay` on failure** and will blow
  the play's timeout in a big fleet. Keep retries tight - and remember a host
  that exhausts its retries is *removed* from the run while the play continues,
  so check the summary for `unreachable` and `failed`, not just the exit code.
- **`with_items` on a dict in modern Ansible iterates keys**; use `with_dict` /
  `loop: "{{ mydict | dict2items }}"` and check the Ansible version's behaviour
  for the keyword you use.
- **Secrets**: `no_log` on every task that touches one, `vault` for the values,
  and a secret store for the vault password. Never commit a decrypted value.
- **`gather_facts: false` plus a `setup` task is not the same as no facts** -
  if a play reads any fact, it needs `gather_facts: true` or an explicit
  `ansible.builtin.setup` before use.
- **Do not put `ignore_errors: true` on a task that creates a resource or a
  user.** It converts a failure into a silent, broken, non-idempotent state.
  If you must, log loudly and assert on the outcome later.

## Safety notes

- Never run a play against production without `--limit` to a canary first, and
  without `--check --diff` before that.
- Set `max_fail_percentage` on fleet plays so a bad role change stops after a
  few hosts instead of corrupting everything.
- Guard destructive operations (`file: state=absent`, `user: state=absent`,
  database drops) behind a `when` on an explicit variable, and default that
  variable to `false` so a missing input cannot delete anything.
