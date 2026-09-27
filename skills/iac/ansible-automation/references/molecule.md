# Molecule reference

Proving a role actually works, and that it is idempotent.

## Why it exists

A role can converge cleanly on the first run and change something on every
subsequent run. Nothing in normal play catches that, because playbooks report
success. Molecule is the only practical way to know a role is idempotent.

## The three phases

A scenario runs **converge → idempotence → side_effect**, then destroys the
instance.

### converge.yml — apply the role

```yaml
---
- name: Converge
  hosts: all
  gather_facts: true
  roles:
    - role: nginx
```

### verify.yml — assert the real result

```yaml
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

Assert on observable state, not on the fact that the play returned `ok`. A role
that runs and does nothing passes a naive verify.

## The idempotence trap

Do **not** read idempotence off a registered `include_role`. The include itself
always reports `changed`, which makes the assertion meaningless. Two approaches
that actually work:

### Option A — converge twice, count the second pass

Re-run the role and assert that the second pass reports no changed tasks, using
a callback or a `set_stats` tally. This is the real check, but it is clunky.

### Option B — check-mode re-run (what most teams ship)

Re-converge in check mode against the already-converged instance:

```yaml
- name: Idempotence - a check-mode re-run must change nothing
  hosts: all
  tasks:
    - name: Re-converge in check mode
      ansible.builtin.import_role: { name: nginx }
      check_mode: true
      diff: true
      register: second_pass

    - name: Assert nothing reported changed
      ansible.builtin.assert:
        that: not (second_pass.changed | default(false))
        fail_msg: >-
          nginx role is not idempotent - the second run wants to change
          something. This is the most common role defect.
```

`import_role` (not `include_role`) is what makes the `changed` flag meaningful
here.

Option B catches the class of bug people actually hit: a task that rewrites a
file, or re-runs a command, every single time.

## side_effect.yml — cleanup

Assert that files, users, or packages the role created are removed on teardown.
Without this, a role that leaks resources passes every other test.

## Commands

```sh
molecule test                      # converge + idempotence + side_effect + destroy
molecule converge -e debug=true   # one phase, with extra vars
molecule idempotence              # the idempotence phase alone
molecule destroy
molecule list                     # available scenarios
molecule matrix                   # all scenarios
```

## Drivers

| Driver | Use when |
|---|---|
| `default` | Local or virtualised instances, no container runtime |
| `docker` | Fast feedback for most roles — this is the default choice |
| `ec2` / `gcloud` | Only when a role's modules genuinely require a cloud API |
| `delegated` | The instance is managed outside Molecule |

Use `docker` unless the role specifically needs a real cloud API. Cloud drivers
are slow and add credentials to the test loop.

## Gotchas

- A scenario's `create.yml` and `destroy.yml` must be symmetrical. A leaked
  resource shows up as a slow or failing destroy, long after the real problem.
- Molecule tests the role, not the playbook. Inventory and variable precedence
  bugs surface only in a real playbook run.
- If `molecule test` is slow because of instance creation, iterate with
  `molecule converge` and `molecule idempotence` only, skipping create/destroy.
- The `docker` driver does not exercise systemd, so a role that manages a
  service will appear to pass. Use a real VM driver for service roles.
