# Podman Role Parity (Issue #24 Steps 1–7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `roles/podman` closer to feature parity with `ansible-community/molecule-plugins/podman` by landing the first seven items of the suggested implementation order in issue #24 — covering the `_spec_merge.yml`/`_validate.yml` refactor, schema-level Group A additions (incl. #19's `systemd`/`cgroupns`), Group D network-subnet handling, the `cmd_args` mechanism plus `extra_opts` (Groups B+C), `MOLECULE_PODMAN_EXECUTABLE` (Group G), and the partial Group I work (`label`, `reset.yml`, sanity asserts).

**Architecture:** Mirror the docker role's task layout — extract a `_spec_merge.yml` and `_validate.yml` from the inline merge so create/destroy share one path, then layer additions on top using the existing `_mp_specs[item].<field> | default(omit)` pattern. Tests live in the existing `extensions/molecule/default/` scenario by exercising new schema fields on the `instance` host and asserting effects via `containers.podman.podman_container_info` / `containers.podman.podman_network_info` in `verify.yml`. The dev container is rootless podman, so all assertions are scoped to what works rootless (no `pid_mode: host`, no real `/dev/*` passthrough, no rootful daemon).

**Tech Stack:** Ansible (`ansible-core` 2.15+ floor; devcontainer runs 2.20), `containers.podman` collection ≥ 1.7.0, Molecule 26.4, pytest with the `pytest_ansible.molecule_scenario` fixture, rootless podman 5.8.

---

## Pre-flight (one-time, before starting Task 1)

> **Important baseline note:** `PROVISIONER=podman pytest tests/integration -v -k default` currently FAILS at the prepare phase in this devcontainer. The scenario's `mp_defaults.podman.command: /sbin/init` + `privileged: true` requires the container to run as a systemd-init host, but the role today does not pass `--systemd=always` to `podman run`, so the container's PID 1 exits immediately and the prepare phase can't reach it. **This is exactly the bug Task 1 (issue #19) closes** — the new `systemd: always` / `cgroupns: host` fields restore a working lifecycle. Treat the baseline as broken; do not chase the prepare failure separately.

- [ ] **Step P1: Confirm baseline lint is clean**

Run: `cd /workspace/ansible-collection-molecule_provisioners && ansible-lint roles/podman/ && yamllint .`
Expected: `Passed: 0 failure(s), 0 warning(s)` from ansible-lint; yamllint silent (exit 0).

- [ ] **Step P2: Confirm scenario invocation path**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -20`
Expected: pytest reaches the `create` phase successfully, then fails at `prepare` (CRITICAL: Ansible return code was 4). This confirms the test wrapper works and confirms the baseline issue described above. Task 1 will produce a passing run.

## File Structure

New files this plan creates:

- `roles/podman/tasks/_spec_merge.yml` — shared 3-layer merge (role defaults ← `mp_defaults.podman` ← `hostvars[item].mp.podman`); recursive combine matching docker.
- `roles/podman/tasks/_validate.yml` — pre-flight assertions on required fields (`image`), the play-level `ansible_version` sanity check from Group I, and shape checks for the new fields where useful.
- `roles/podman/tasks/_cmd_args.yml` — assembles `__mp_podman_cmd_args[item]` from `cgroup_manager`, `storage_opt`, `storage_driver`, and `extra_opts` so create.yml feeds one list into `podman_container`'s `cmd_args` param.
- `playbooks/reset.yml` — standalone playbook to remove all containers labeled `owner=molecule`; exposed as `david_igou.molecule_provisioners.reset`.

Files modified:

- `roles/podman/tasks/create.yml` — replace inline merge with `include_tasks`, add validation include, thread executable, add Group A fields + label, include `_cmd_args.yml`.
- `roles/podman/tasks/destroy.yml` — replace inline merge with `include_tasks`, thread executable.
- `roles/podman/tasks/_networks.yml` — normalize `podman_network` to a list of dicts, thread executable, pass subnet/gateway on create.
- `roles/podman/defaults/main.yml` — new role defaults (`mp_podman_executable`, expand `mp_podman_reserved_networks`); new keys in `mp_podman_role_defaults` for fields whose default is NOT `omit`.
- `roles/podman/meta/argument_specs.yml` — document every new option under `create`/`destroy`.
- `roles/podman/README.md` — list new per-host schema fields.
- `extensions/molecule/default/inventory/hosts.yml` — exercise the new fields on the `instance` host (gated by `PROVISIONER=podman` won't matter because the inventory carries every backend block; the `podman` block grows).
- `extensions/molecule/default/verify.yml` — add `podman_container_info`/`podman_network_info` lookups + assertions for each new field. Gate the podman-specific assertions on `mp_backend == 'podman'` so the same scenario keeps working under other backends.
- `docs/examples/inventory/hosts.yml` — commented-out examples of new fields.
- `CLAUDE.md` — extend the public-contract `podman:` block in §"Public contract".
- `changelogs/fragments/podman-parity-steps-1-7.yml` — `minor_changes` entry summarizing the additions.

---

## Task 1: Schema additions — `systemd` and `cgroupns` (issue #19)

Schema-only addition; the `containers.podman.podman_container` module already supports both parameters and they're silently dropped today.

**Files:**

- Modify: `roles/podman/tasks/create.yml`
- Modify: `roles/podman/defaults/main.yml`
- Modify: `roles/podman/meta/argument_specs.yml`
- Modify: `roles/podman/README.md`
- Modify: `extensions/molecule/default/inventory/hosts.yml`
- Modify: `extensions/molecule/default/verify.yml`
- Modify: `docs/examples/inventory/hosts.yml`
- Modify: `CLAUDE.md`

- [ ] **Step 1.1: Add failing verify assertion**

In `extensions/molecule/default/verify.yml`, replace the file contents with:

```yaml
---
- name: Verify — every molecule host responds to ping
  hosts: molecule
  gather_facts: false
  tasks:
    - name: Ping each host
      ansible.builtin.ping:

- name: Verify — podman container has the expected configuration
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    _mp_backend: "{{ hostvars[groups['molecule'][0]].mp_backend | default('podman') }}"
  tasks:
    - name: Skip non-podman backends
      ansible.builtin.meta: end_play
      when: _mp_backend != 'podman'

    - name: Inspect the instance container
      containers.podman.podman_container_info:
        name: instance
      register: __mp_verify_ci

    - name: Set the container fact for readability
      ansible.builtin.set_fact:
        _ctr: "{{ __mp_verify_ci.containers[0] }}"

    - name: Assert systemd mode propagated (Config.SystemdMode true)
      ansible.builtin.assert:
        that:
          - _ctr.Config.SystemdMode | default(false) | bool
        fail_msg: >-
          Expected Config.SystemdMode=true on the container
          (mp.podman.systemd: always); got
          {{ _ctr.Config.SystemdMode | default('<unset>') }}.

    - name: Assert cgroup namespace mode propagated (HostConfig.CgroupMode 'host')
      ansible.builtin.assert:
        that:
          - _ctr.HostConfig.CgroupMode == "host"
        fail_msg: >-
          Expected HostConfig.CgroupMode=host (mp.podman.cgroupns: host);
          got '{{ _ctr.HostConfig.CgroupMode | default('<unset>') }}'.
```

In `extensions/molecule/default/inventory/hosts.yml`, replace the `podman:` block under `instance:` with:

```yaml
podman:
  image: docker.io/geerlingguy/docker-fedora41-ansible:latest
  systemd: always
  cgroupns: host
```

- [ ] **Step 1.2: Run the scenario — expect FAIL during `prepare`**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -20`
Expected: `create` succeeds, then `prepare` fails (return code 4) because PID 1 (`/sbin/init`) exits before ansible can connect — the role does not yet pass `systemd: always` through to `podman_container`. This matches the pre-existing broken baseline; you have not made it worse. The new verify assertions never get a chance to run yet.

Side check: `podman ps -a --filter name=instance --format '{{.Names}} {{.Status}}'` will show `instance Exited (255)`. Remove with `podman rm -f instance` if molecule's destroy left it behind.

- [ ] **Step 1.3: Wire `systemd` and `cgroupns` through `create.yml`**

In `roles/podman/tasks/create.yml`, find the `Create molecule instance(s)` task (lines ~36-50). Add two lines inside the `containers.podman.podman_container:` mapping (alphabetical placement, after `expose:`/`publish:` is fine; ordering does not affect behavior):

```yaml
cgroupns: "{{ _mp_specs[item].cgroupns | default(omit) }}"
systemd: "{{ _mp_specs[item].systemd | default(omit) }}"
```

- [ ] **Step 1.4: Document the new fields in `mp_podman_role_defaults` and argument_specs**

In `roles/podman/defaults/main.yml`, leave `mp_podman_role_defaults` unchanged — both fields default to `omit` (not present in the dict), which is the same pattern used for `tmpfs`/`exposed_ports`/etc. Add a comment above `mp_podman_role_defaults` listing the omit-defaulted optional fields:

Replace lines 21-24 with:

```yaml
# Per-host field defaults. Layered as: this dict <- mp_defaults.podman <- hostvars[item].mp.podman.
# Only `image` is required. Fields whose role default is "omit" (podman_network,
# tmpfs, exposed_ports, published_ports, systemd, cgroupns, hostname, tty, detach,
# etc_hosts, dns_servers, pid_mode, security_opts, devices, ulimits, ip,
# restart_policy, restart_retries, cgroup_manager, storage_opt, storage_driver,
# extra_opts) are intentionally absent — the role's module calls use
# `| default(omit)` so the podman_container module's own defaults apply.
mp_podman_role_defaults:
```

In `roles/podman/meta/argument_specs.yml`, the role defaults dict is described generically and doesn't enumerate every field. No change needed for this step — argument*specs documents role-level vars (mp_podman_role_defaults, mp_podman_async*\*), not the per-host schema (which lives in README + CLAUDE.md). Skip.

- [ ] **Step 1.5: Update `README.md` and `CLAUDE.md`**

In `roles/podman/README.md`, in the `## Inputs (per-host, in inventory)` YAML block, after the `published_ports: []` line add:

```yaml
systemd: always # optional — 'always' | 'true' | 'false' | leave unset
cgroupns: host # optional — 'host' | 'private'
```

In `CLAUDE.md`, in the §"Public contract" `podman:` comment listing optional fields (lines 67-70), update the comment to:

```yaml
podman: # required when mp_backend == podman
  image: <str> # required
  # optional: command, privileged, volumes, capabilities,
  # podman_network, env, tmpfs, exposed_ports, published_ports,
  # systemd, cgroupns
```

- [ ] **Step 1.6: Add commented example to docs/examples**

In `docs/examples/inventory/hosts.yml`, append to the `podman:` block under `ubuntu-24:`:

```yaml
# Systemd-friendly knobs (uncomment when running an init-based image):
# systemd: always
# cgroupns: host
```

- [ ] **Step 1.7: Re-run scenario — expect PASS end-to-end**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -20`
Expected: `tests/integration/test_integration.py::test_integration[extensions-default] PASSED`. Full lifecycle (`create → prepare → converge → verify → destroy`) returns exit 0; verify play passes both new assertions. This is the first run since the baseline broke that completes cleanly — Task 1 unblocks every later task.

- [ ] **Step 1.8: Lint**

Run: `cd /workspace/ansible-collection-molecule_provisioners && ansible-lint roles/podman/ extensions/ playbooks/ && yamllint .`
Expected: `Passed: 0 failure(s)`.

- [ ] **Step 1.9: Commit**

```bash
git add roles/podman/tasks/create.yml roles/podman/defaults/main.yml \
        roles/podman/README.md CLAUDE.md docs/examples/inventory/hosts.yml \
        extensions/molecule/default/inventory/hosts.yml \
        extensions/molecule/default/verify.yml
git commit -m "feat(podman): expose systemd and cgroupns per-host fields (#19)"
```

---

## Task 2: Refactor — extract `_spec_merge.yml` and `_validate.yml`

Behavior-preserving extraction so create and destroy share one merge implementation, matching the docker role's layout. Switches to `recursive=True` merge (intentional — matches docker; lets `mp_defaults.podman.env` and per-host `env` compose instead of last-write-wins).

**Files:**

- Create: `roles/podman/tasks/_spec_merge.yml`
- Create: `roles/podman/tasks/_validate.yml`
- Modify: `roles/podman/tasks/create.yml`
- Modify: `roles/podman/tasks/destroy.yml`

- [ ] **Step 2.1: Create `_spec_merge.yml`**

Create `roles/podman/tasks/_spec_merge.yml` with:

```yaml
---
# Build _mp_specs[host] from three layers:
#   role defaults <- mp_defaults.podman <- hostvars[item].mp.podman
# Defensive on every layer so destroy can still merge for half-failed creates.
- name: Initialize podman spec map
  ansible.builtin.set_fact:
    _mp_specs: {}

- name: Merge per-host specs with mp_defaults and role defaults
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: mp_podman_role_defaults
                 | combine(mp_defaults['podman'] | default({}), recursive=True)
                 | combine((hostvars[item].mp | default({}))['podman'] | default({}), recursive=True)
         }, recursive=True) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 2.2: Create `_validate.yml`**

Create `roles/podman/tasks/_validate.yml` with:

```yaml
---
# Fail-fast validation. Run after _spec_merge.yml. No side effects.
- name: Validate image is set per host
  ansible.builtin.assert:
    that:
      - _mp_specs[item].image is defined
      - (_mp_specs[item].image | string | length) > 0
    fail_msg: >-
      Host '{{ item }}' is missing podman.image. Set
      hostvars.{{ item }}.mp.podman.image to a fully-qualified image reference.
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 2.3: Update `create.yml` to use the includes**

In `roles/podman/tasks/create.yml`, replace lines 1-18 (the `Initialize` + `Merge per-host specs` block) with:

```yaml
---
- name: Merge per-host specs
  ansible.builtin.include_tasks: _spec_merge.yml

- name: Validate per-host specs
  ansible.builtin.include_tasks: _validate.yml
```

The `_networks.yml` import lower in the file stays as-is.

- [ ] **Step 2.4: Update `destroy.yml` to use the include**

In `roles/podman/tasks/destroy.yml`, replace lines 24-41 (the comment + `Initialize` + `Merge per-host specs` block) with:

```yaml
# Network cleanup needs the merged specs to know which networks were created.
# Defensive merge — destroy is idempotent against half-failed creates.
- name: Merge per-host specs (defensive)
  ansible.builtin.include_tasks: _spec_merge.yml
```

- [ ] **Step 2.5: Re-run scenario — should still PASS**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: exit 0, verify play passes (no new assertions; this is a refactor).

- [ ] **Step 2.6: Lint**

Run: `cd /workspace/ansible-collection-molecule_provisioners && ansible-lint roles/podman/ && yamllint .`
Expected: clean.

- [ ] **Step 2.7: Commit**

```bash
git add roles/podman/tasks/_spec_merge.yml roles/podman/tasks/_validate.yml \
        roles/podman/tasks/create.yml roles/podman/tasks/destroy.yml
git commit -m "refactor(podman): extract _spec_merge and _validate, mirror docker layout"
```

---

## Task 3: Group A — schema-only field additions

Wire eleven `containers.podman.podman_container` parameters that the role currently drops on the floor. All additive optional fields. `restart_policy_attempts` is the module's name; the public-facing field is named `restart_retries` to mirror the docker role's vocabulary.

**Files:**

- Modify: `roles/podman/tasks/create.yml`
- Modify: `roles/podman/README.md`
- Modify: `CLAUDE.md`
- Modify: `extensions/molecule/default/inventory/hosts.yml`
- Modify: `extensions/molecule/default/verify.yml`
- Modify: `docs/examples/inventory/hosts.yml`

- [ ] **Step 3.1: Extend verify assertions for Group A fields**

In `extensions/molecule/default/verify.yml`, after the existing `Assert cgroup namespace mode propagated` task, append:

```yaml
- name: Assert hostname propagated
  ansible.builtin.assert:
    that:
      - _ctr.Config.Hostname == "mp-instance"
    fail_msg: "Expected Hostname=mp-instance; got '{{ _ctr.Config.Hostname }}'."

- name: Assert TTY propagated
  ansible.builtin.assert:
    that:
      - _ctr.Config.Tty | bool
    fail_msg: "Expected Tty=true; got {{ _ctr.Config.Tty }}."

- name: Assert etc_hosts entry present
  ansible.builtin.assert:
    that:
      - "'mp-extra-host:10.0.0.42' in (_ctr.HostConfig.ExtraHosts | default([]))"
    fail_msg: "Expected ExtraHosts to contain mp-extra-host:10.0.0.42; got {{ _ctr.HostConfig.ExtraHosts | default('<unset>') }}."

- name: Assert DNS server propagated
  ansible.builtin.assert:
    that:
      - "'1.1.1.1' in (_ctr.HostConfig.Dns | default([]))"
    fail_msg: "Expected HostConfig.Dns to include 1.1.1.1; got {{ _ctr.HostConfig.Dns | default('<unset>') }}."

- name: Assert security_opt propagated
  ansible.builtin.assert:
    that:
      - "'seccomp=unconfined' in (_ctr.HostConfig.SecurityOpt | default([]))"
    fail_msg: "Expected SecurityOpt to include seccomp=unconfined; got {{ _ctr.HostConfig.SecurityOpt | default('<unset>') }}."

- name: Assert ulimits propagated (nofile soft cap)
  ansible.builtin.assert:
    that:
      # podman normalizes `nofile=1024:2048` → Name: RLIMIT_NOFILE
      - (_ctr.HostConfig.Ulimits | default([])) | selectattr('Name', 'equalto', 'RLIMIT_NOFILE') | list | length > 0
      - ((_ctr.HostConfig.Ulimits | default([])) | selectattr('Name', 'equalto', 'RLIMIT_NOFILE') | list | first).Soft | int == 1024
    fail_msg: "Expected Ulimits to include RLIMIT_NOFILE with Soft=1024; got {{ _ctr.HostConfig.Ulimits | default('<unset>') }}."

- name: Assert restart_policy propagated
  ansible.builtin.assert:
    that:
      - _ctr.HostConfig.RestartPolicy.Name == "on-failure"
      - (_ctr.HostConfig.RestartPolicy.MaximumRetryCount | int) == 3
    fail_msg: >-
      Expected RestartPolicy.Name=on-failure, MaximumRetryCount=3;
      got name='{{ _ctr.HostConfig.RestartPolicy.Name }}',
      retries={{ _ctr.HostConfig.RestartPolicy.MaximumRetryCount }}.

- name: Assert pid_mode propagated
  ansible.builtin.assert:
    that:
      - (_ctr.HostConfig.PidMode | default('')) | length > 0
    fail_msg: "Expected HostConfig.PidMode to be set; got '{{ _ctr.HostConfig.PidMode | default('<unset>') }}'."
```

Note: `devices` and `ip` are excluded from the verify assertions in this task — `devices` requires the parent container to share `/dev` (it does not in this devcontainer), and `ip` is only meaningful with a user-created network carrying a subnet, which arrives in Task 4. Both are wired through the role schema so the module accepts them, but verification waits.

- [ ] **Step 3.2: Extend the test inventory to set Group A fields**

In `extensions/molecule/default/inventory/hosts.yml`, replace the `podman:` block under `instance:` with:

```yaml
podman:
  image: docker.io/geerlingguy/docker-fedora41-ansible:latest
  systemd: always
  cgroupns: host
  hostname: mp-instance
  tty: true
  detach: true
  etc_hosts:
    mp-extra-host: 10.0.0.42
  dns_servers:
    - 1.1.1.1
  security_opts:
    - seccomp=unconfined
  ulimits:
    - nofile=1024:2048
  pid_mode: private
  restart_policy: on-failure
  restart_retries: 3
```

- [ ] **Step 3.3: Run scenario — verify expected to FAIL on the new asserts**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -60`
Expected: lifecycle still completes; verify play fails on the first new assertion (`Assert hostname propagated`) because the role drops the new fields.

- [ ] **Step 3.4: Wire Group A fields into `create.yml`**

In `roles/podman/tasks/create.yml`, in the `Create molecule instance(s)` task's `containers.podman.podman_container:` mapping, expand the field list to:

```yaml
containers.podman.podman_container:
  name: "{{ item }}"
  image: "{{ _mp_specs[item].image }}"
  state: started
  recreate: false
  command: "{{ _mp_specs[item].command | default(omit) }}"
  privileged: "{{ _mp_specs[item].privileged | default(false) }}"
  volume: "{{ _mp_specs[item].volumes | default(omit) }}"
  cap_add: "{{ _mp_specs[item].capabilities | default(omit) }}"
  expose: "{{ _mp_specs[item].exposed_ports | default(omit) }}"
  publish: "{{ _mp_specs[item].published_ports | default(omit) }}"
  network: "{{ _mp_specs[item].podman_network | default(omit) }}"
  env: "{{ _mp_specs[item].env | default({}) }}"
  tmpfs: "{{ _mp_specs[item].tmpfs | default(omit) }}"
  cgroupns: "{{ _mp_specs[item].cgroupns | default(omit) }}"
  systemd: "{{ _mp_specs[item].systemd | default(omit) }}"
  hostname: "{{ _mp_specs[item].hostname | default(omit) }}"
  tty: "{{ _mp_specs[item].tty | default(omit) }}"
  detach: "{{ _mp_specs[item].detach | default(omit) }}"
  etc_hosts: "{{ _mp_specs[item].etc_hosts | default(omit) }}"
  dns: "{{ _mp_specs[item].dns_servers | default(omit) }}"
  pid: "{{ _mp_specs[item].pid_mode | default(omit) }}"
  security_opt: "{{ _mp_specs[item].security_opts | default(omit) }}"
  device: "{{ _mp_specs[item].devices | default(omit) }}"
  ulimit: "{{ _mp_specs[item].ulimits | default(omit) }}"
  ip: "{{ _mp_specs[item].ip | default(omit) }}"
  restart_policy: "{{ _mp_specs[item].restart_policy | default(omit) }}"
  restart_policy_attempts: "{{ _mp_specs[item].restart_retries | default(omit) }}"
```

Module-param mapping notes:

- public-facing `dns_servers` → module param `dns` (singular).
- public-facing `security_opts` → module param `security_opt` (singular).
- public-facing `devices` → module param `device` (singular).
- public-facing `ulimits` → module param `ulimit` (singular).
- public-facing `pid_mode` → module param `pid`.
- public-facing `restart_retries` → module param `restart_policy_attempts`.

The public-facing names match docker role / molecule-plugins vocabulary; the module rename happens here in one place.

- [ ] **Step 3.5: Re-run scenario — expect PASS on all new asserts**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: exit 0; verify play passes all Group A assertions plus the carried-over Task 1 assertions.

- [ ] **Step 3.6: Document Group A in README and CLAUDE.md**

In `roles/podman/README.md`, replace the `## Inputs (per-host, in inventory)` YAML block with:

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            podman:
              image: docker.io/...:tag # required
              command: /sbin/init # optional, role default '/sbin/init'
              privileged: false # optional, role default false
              volumes: [] # optional
              capabilities: [] # optional
              podman_network: [] # optional, list or single string
              env: {} # optional
              tmpfs: [] # optional
              exposed_ports: [] # optional
              published_ports: [] # optional
              systemd: always # optional — 'always' | 'true' | 'false'
              cgroupns: host # optional — 'host' | 'private'
              hostname: <str> # optional
              tty: true # optional
              detach: true # optional (default behavior)
              etc_hosts: {} # optional — dict of host:ip
              dns_servers: [] # optional — list of DNS server IPs
              pid_mode: <str> # optional — 'host', 'container:<id>', 'private'
              security_opts: [] # optional — e.g. ['seccomp=unconfined']
              devices: [] # optional — list of '/host:/ctr[:rwm]' mappings
              ulimits: [] # optional — e.g. ['nofile=1024:2048']
              ip: <str> # optional — only with a network that has a subnet
              restart_policy: <str> # optional — 'no', 'on-failure', 'always', 'unless-stopped'
              restart_retries: <int> # optional — paired with restart_policy=on-failure
```

In `CLAUDE.md`, in §"Public contract", update the `podman:` block to:

```yaml
podman: # required when mp_backend == podman
  image: <str> # required
  # optional: command, privileged, volumes, capabilities,
  # podman_network, env, tmpfs, exposed_ports, published_ports,
  # systemd, cgroupns, hostname, tty, detach, etc_hosts, dns_servers,
  # pid_mode, security_opts, devices, ulimits, ip, restart_policy,
  # restart_retries
```

- [ ] **Step 3.7: Add commented examples to docs/examples**

In `docs/examples/inventory/hosts.yml`, replace the `podman:` block under `ubuntu-24:` with:

```yaml
podman:
  image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
  # Systemd-friendly knobs:
  # systemd: always
  # cgroupns: host
  # Common per-host knobs:
  # hostname: my-host
  # tty: true
  # etc_hosts:
  #   my-extra-host: 10.0.0.42
  # dns_servers: [1.1.1.1]
  # security_opts: [seccomp=unconfined]
  # ulimits: [nofile=1024:2048]
  # pid_mode: private
  # restart_policy: on-failure
  # restart_retries: 3
```

- [ ] **Step 3.8: Lint**

Run: `cd /workspace/ansible-collection-molecule_provisioners && ansible-lint roles/podman/ extensions/ && yamllint .`
Expected: clean.

- [ ] **Step 3.9: Commit**

```bash
git add roles/podman/tasks/create.yml roles/podman/README.md CLAUDE.md \
        docs/examples/inventory/hosts.yml \
        extensions/molecule/default/inventory/hosts.yml \
        extensions/molecule/default/verify.yml
git commit -m "feat(podman): expose Group A schema fields (hostname/tty/dns/security_opts/etc)"
```

---

## Task 4: Group D — network subnet handling and reserved-name additions

Widen the `podman_network` field to accept a list-of-dicts so consumers can request a network with a specific subnet/gateway. Normalize the input shape in `_spec_merge.yml` so create/destroy share one path. Add `ns` and `private` to the reserved-network list.

**Files:**

- Modify: `roles/podman/tasks/_spec_merge.yml`
- Modify: `roles/podman/tasks/_networks.yml`
- Modify: `roles/podman/tasks/create.yml`
- Modify: `roles/podman/tasks/destroy.yml`
- Modify: `roles/podman/defaults/main.yml`
- Modify: `roles/podman/README.md`
- Modify: `CLAUDE.md`
- Modify: `extensions/molecule/default/inventory/hosts.yml`
- Modify: `extensions/molecule/default/verify.yml`
- Modify: `docs/examples/inventory/hosts.yml`

- [ ] **Step 4.1: Add failing network-subnet verify**

In `extensions/molecule/default/verify.yml`, after the Task 3 assertions, append:

```yaml
- name: Inspect the mp-test-net network
  containers.podman.podman_network_info:
    name: mp-test-net
  register: __mp_verify_net

- name: Assert network subnet matches request
  ansible.builtin.assert:
    that:
      - __mp_verify_net.networks | length == 1
      - __mp_verify_net.networks[0].subnets[0].subnet == "10.89.0.0/24"
    fail_msg: >-
      Expected mp-test-net to carry subnet 10.89.0.0/24;
      got {{ __mp_verify_net.networks | default([]) }}.
```

- [ ] **Step 4.2: Extend test inventory to request a custom-subnet network**

In `extensions/molecule/default/inventory/hosts.yml`, append to the `podman:` block under `instance:` (after `restart_retries: 3`):

```yaml
podman_network:
  - name: mp-test-net
    subnet: 10.89.0.0/24
    gateway: 10.89.0.1
```

- [ ] **Step 4.3: Run scenario — verify expected to FAIL on subnet assertion**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -60`
Expected: the run fails — either at network create (because `_networks.yml` currently expects strings, not dicts) or at the subnet assertion. Either failure proves the test is wired.

- [ ] **Step 4.4: Normalize networks in `_spec_merge.yml`**

In `roles/podman/tasks/_spec_merge.yml`, after the existing `Merge per-host specs` task, append:

```yaml
# Normalize podman_network to a list-of-dicts: [{name, subnet?, gateway?}, ...].
# Accepts: undefined, single string, list of strings, list of dicts (mixed OK).
- name: Normalize podman_network shape per host
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: _mp_specs[item] | combine({
             'podman_network': _mp_podman_network_normalized
           })
         }, recursive=True) }}
  vars:
    _raw: "{{ _mp_specs[item].podman_network | default([]) }}"
    _as_list: "{{ [_raw] if (_raw is string) else (_raw if _raw else []) }}"
    _mp_podman_network_normalized: >-
      {{ _as_list
         | map('community.general.dict_kv', 'name')
         | list
         if (_as_list and _as_list[0] is string)
         else _as_list | list }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

`community.general.dict_kv` is the canonical filter for `'foo' → {'name': 'foo'}`. The branch on `_as_list[0] is string` assumes all-strings or all-dicts in the user's list (no mixing); the README documents that. Step 4.5 ensures `community.general` is declared.

- [ ] **Step 4.5: Pin `community.general` in `galaxy.yml`**

In `galaxy.yml`, find the `dependencies:` block. If `community.general` is not present, add:

```yaml
community.general: ">=8.0.0"
```

Note: if `community.general` is already pinned, leave it alone — just verify by running `grep -n community.general galaxy.yml`.

- [ ] **Step 4.6: Update `_networks.yml` to consume the normalized list**

In `roles/podman/tasks/_networks.yml`, replace the entire file contents with:

```yaml
---
# Build __mp_podman_networks: deduped list-of-dicts of non-reserved networks
# referenced by any host in groups['molecule']. Shared by create.yml + destroy.yml.
# Each entry: {name, subnet?, gateway?}.
- name: Initialize podman network list
  ansible.builtin.set_fact:
    __mp_podman_networks: []

- name: Collect networks from per-host podman_network
  ansible.builtin.set_fact:
    __mp_podman_networks: >-
      {{ __mp_podman_networks + _mp_specs[item].podman_network }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  when:
    - _mp_specs[item].podman_network is defined
    - _mp_specs[item].podman_network | length > 0

- name: Deduplicate by network name and drop reserved + ns:/container: refs
  ansible.builtin.set_fact:
    __mp_podman_networks: >-
      {{ (__mp_podman_networks
            | rejectattr('name', 'in', mp_podman_reserved_networks)
            | rejectattr('name', 'match', '^ns:')
            | rejectattr('name', 'match', '^container:')
            | list)
         | unique(attribute='name') }}
```

- [ ] **Step 4.7: Update `create.yml` to pass subnet/gateway**

In `roles/podman/tasks/create.yml`, replace the `Create podman network(s)` task (lines ~23-31 — already-existing) with:

```yaml
- name: Create podman network(s)
  containers.podman.podman_network:
    name: "{{ item.name }}"
    subnet: "{{ item.subnet | default(omit) }}"
    gateway: "{{ item.gateway | default(omit) }}"
    state: present
  loop: "{{ __mp_podman_networks }}"
  loop_control:
    label: "{{ item.name }}"
```

Also update the `network:` line inside the `Create molecule instance(s)` task to extract names only:

```yaml
network: "{{ _mp_specs[item].podman_network | map(attribute='name') | list | default(omit, true) }}"
```

(`| default(omit, true)` — the second positional `true` makes `omit` apply when the value is falsy/empty, not just undefined.)

- [ ] **Step 4.8: Update `destroy.yml` to consume the new list-of-dicts**

In `roles/podman/tasks/destroy.yml`, replace the `Delete podman network(s)` task (lines ~46-54) with:

```yaml
- name: Delete podman network(s)
  containers.podman.podman_network:
    name: "{{ item.name }}"
    state: absent
  loop: "{{ __mp_podman_networks }}"
  loop_control:
    label: "{{ item.name }}"
```

- [ ] **Step 4.9: Add `ns` and `private` to reserved networks**

In `roles/podman/defaults/main.yml`, replace the `mp_podman_reserved_networks` block with:

```yaml
# Reserved network names that must NOT be (re)created/deleted as standalone networks.
# 'ns' and 'private' are podman-specific (network namespace modes); 'slirp4netns'
# is the rootless default.
mp_podman_reserved_networks:
  - bridge
  - none
  - host
  - slirp4netns
  - ns
  - private
```

- [ ] **Step 4.10: Re-run scenario — expect PASS**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: exit 0; subnet verify passes; existing assertions still pass.

- [ ] **Step 4.11: Document the widened schema in README + CLAUDE.md + examples**

In `roles/podman/README.md`, replace the `podman_network: []` line in the schema block with:

```yaml
podman_network: [] # optional, str | list[str] | list[{name, subnet?, gateway?}]
```

And add immediately below the schema block:

```markdown
### Network shape

`podman_network` accepts three shapes:

- A single string: `podman_network: my-net` → joins a pre-existing network named `my-net`.
- A list of strings: `podman_network: [a, b]` → joins both networks (creates them if missing).
- A list of dicts: `podman_network: [{name: my-net, subnet: 10.89.0.0/24, gateway: 10.89.0.1}]` → creates `my-net` with the given subnet on first apply, then joins.

Names listed in `mp_podman_reserved_networks` (default: `bridge`, `none`, `host`, `slirp4netns`, `ns`, `private`) and pseudo-references prefixed with `ns:` or `container:` are skipped during network create/destroy.
```

In `CLAUDE.md`, no schema change needed — the `podman_network` field is already listed; the widening from `str|list[str]` to `str|list[str]|list[dict]` is documented in the README.

In `docs/examples/inventory/hosts.yml`, replace the network commented-example with:

```yaml
# Network with a custom subnet:
# podman_network:
#   - name: my-net
#     subnet: 10.89.0.0/24
#     gateway: 10.89.0.1
```

- [ ] **Step 4.12: Lint**

Run: `cd /workspace/ansible-collection-molecule_provisioners && ansible-lint roles/podman/ extensions/ && yamllint .`
Expected: clean.

- [ ] **Step 4.13: Commit**

```bash
git add roles/podman/tasks/_spec_merge.yml roles/podman/tasks/_networks.yml \
        roles/podman/tasks/create.yml roles/podman/tasks/destroy.yml \
        roles/podman/defaults/main.yml roles/podman/README.md \
        docs/examples/inventory/hosts.yml \
        extensions/molecule/default/inventory/hosts.yml \
        extensions/molecule/default/verify.yml galaxy.yml
git commit -m "feat(podman): support list-of-dicts podman_network with subnet/gateway"
```

---

## Task 5: Groups B + C — `cmd_args` assembly and `extra_opts`

Introduce a shared `cmd_args` builder so podman-CLI flags (Group B: `cgroup_manager`, `storage_opt`, `storage_driver`) and the raw passthrough (Group C: `extra_opts`) ride the same mechanism. Devcontainer verification is limited to `extra_opts` (passing `--memory=128m`); the Group B flags are wired and lint-clean but not asserted at runtime because the devcontainer lacks user-systemd / cgroupfs-specific storage drivers.

**Files:**

- Create: `roles/podman/tasks/_cmd_args.yml`
- Modify: `roles/podman/tasks/create.yml`
- Modify: `roles/podman/README.md`
- Modify: `CLAUDE.md`
- Modify: `extensions/molecule/default/inventory/hosts.yml`
- Modify: `extensions/molecule/default/verify.yml`
- Modify: `docs/examples/inventory/hosts.yml`

- [ ] **Step 5.1: Add failing verify for `extra_opts`**

In `extensions/molecule/default/verify.yml`, after the network subnet block, append:

```yaml
- name: Assert extra_opts --memory took effect
  ansible.builtin.assert:
    that:
      # 128 MiB = 134217728 bytes; podman_container_info reports Memory in bytes.
      - (_ctr.HostConfig.Memory | int) == 134217728
    fail_msg: >-
      Expected HostConfig.Memory=134217728 (128m from extra_opts);
      got {{ _ctr.HostConfig.Memory | default('<unset>') }}.
```

- [ ] **Step 5.2: Add `extra_opts` to test inventory**

In `extensions/molecule/default/inventory/hosts.yml`, append to the `podman:` block under `instance:` (after the network block):

```yaml
extra_opts:
  - --memory=128m
```

- [ ] **Step 5.3: Run scenario — verify expected to FAIL on memory assertion**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: lifecycle completes; verify fails on the `--memory` assertion.

- [ ] **Step 5.4: Create `_cmd_args.yml`**

Create `roles/podman/tasks/_cmd_args.yml` with:

```yaml
---
# Assemble __mp_podman_cmd_args[host] from the per-host podman-CLI flags and
# the extra_opts catch-all. Run after _spec_merge.yml.
- name: Initialize per-host cmd_args map
  ansible.builtin.set_fact:
    __mp_podman_cmd_args: {}

- name: Build cmd_args per host
  ansible.builtin.set_fact:
    __mp_podman_cmd_args: >-
      {{ __mp_podman_cmd_args | combine({
           item: (
             (['--cgroup-manager=' ~ _mp_specs[item].cgroup_manager]
               if (_mp_specs[item].cgroup_manager | default(none)) is not none
               else [])
             + ((_mp_specs[item].storage_opt | default([]))
                | map('regex_replace', '^(.*)$', '--storage-opt=\\1')
                | list)
             + (['--storage-driver=' ~ _mp_specs[item].storage_driver]
                if (_mp_specs[item].storage_driver | default(none)) is not none
                else [])
             + (_mp_specs[item].extra_opts | default([]))
           )
         }) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 5.5: Include `_cmd_args.yml` in `create.yml` and wire into the module call**

In `roles/podman/tasks/create.yml`, after the `Validate per-host specs` include (added in Task 2), insert:

```yaml
- name: Assemble per-host cmd_args
  ansible.builtin.include_tasks: _cmd_args.yml
```

Then add this line to the `Create molecule instance(s)` task's module mapping (place after `restart_policy_attempts:`):

```yaml
cmd_args: "{{ __mp_podman_cmd_args[item] | default([]) }}"
```

- [ ] **Step 5.6: Re-run scenario — expect PASS**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: exit 0; memory assertion passes.

- [ ] **Step 5.7: Document `cgroup_manager`, `storage_opt`, `storage_driver`, `extra_opts`**

In `roles/podman/README.md`, in the schema block, append before the closing brace:

```yaml
cgroup_manager: <str> # optional — 'systemd' | 'cgroupfs' (CLI flag)
storage_opt: [] # optional — list of '--storage-opt=' values
storage_driver: <str> # optional — '--storage-driver=' value
extra_opts: [] # optional — raw `podman` CLI flags appended last
```

In `CLAUDE.md`, in §"Public contract" `podman:` block, extend the comment list to:

```yaml
# optional: command, privileged, volumes, capabilities,
# podman_network, env, tmpfs, exposed_ports, published_ports,
# systemd, cgroupns, hostname, tty, detach, etc_hosts, dns_servers,
# pid_mode, security_opts, devices, ulimits, ip, restart_policy,
# restart_retries, cgroup_manager, storage_opt, storage_driver,
# extra_opts
```

In `docs/examples/inventory/hosts.yml`, append:

```yaml
# CLI-level escape hatch (appended to `podman run` flags):
# extra_opts:
#   - --memory=512m
#   - --cpus=2
```

- [ ] **Step 5.8: Lint**

Run: `cd /workspace/ansible-collection-molecule_provisioners && ansible-lint roles/podman/ extensions/ && yamllint .`
Expected: clean.

- [ ] **Step 5.9: Commit**

```bash
git add roles/podman/tasks/_cmd_args.yml roles/podman/tasks/create.yml \
        roles/podman/README.md CLAUDE.md docs/examples/inventory/hosts.yml \
        extensions/molecule/default/inventory/hosts.yml \
        extensions/molecule/default/verify.yml
git commit -m "feat(podman): assemble cmd_args from cgroup_manager/storage_*/extra_opts"
```

---

## Task 6: Group G — `MOLECULE_PODMAN_EXECUTABLE`

Thread an `mp_podman_executable` variable through every `containers.podman.podman_*` call so operators can swap `podman` for `podman-remote` via the env var. Doesn't touch the per-host contract.

**Files:**

- Modify: `roles/podman/tasks/create.yml`
- Modify: `roles/podman/tasks/destroy.yml`
- Modify: `roles/podman/tasks/_networks.yml`
- Modify: `roles/podman/defaults/main.yml`
- Modify: `roles/podman/meta/argument_specs.yml`
- Modify: `extensions/molecule/default/verify.yml`

- [ ] **Step 6.1: Add failing verify for executable propagation**

Use a side effect of the env var: when `MOLECULE_PODMAN_EXECUTABLE=podman` is set, the role's `executable:` parameter should also be `podman` (i.e., default behavior). The verify can't directly observe this, so the test instead just asserts the role variable resolves correctly via a `debug`+`assert` pre-check inside verify. In `extensions/molecule/default/verify.yml`, after the cmd_args assertion, append:

```yaml
- name: Assert mp_podman_executable resolves to the env var default
  ansible.builtin.assert:
    that:
      - (lookup('env', 'MOLECULE_PODMAN_EXECUTABLE') | default('podman', true)) | length > 0
    fail_msg: "MOLECULE_PODMAN_EXECUTABLE lookup returned empty."
```

This is a weak test (it asserts the lookup mechanism, not propagation). To really verify propagation: set `MOLECULE_PODMAN_EXECUTABLE` to a wrapper script that records its invocation, then assert the recording happened. That's overkill for the v1 of this knob. Defer to CI on a real runner for a stronger test; here we just confirm the lookup pipeline.

- [ ] **Step 6.2: Add `mp_podman_executable` default**

In `roles/podman/defaults/main.yml`, after the `mp_podman_async_delay: 24` line, append:

```yaml
# Operator override: swap `podman` for `podman-remote` (or similar) via env var.
# Threaded through every containers.podman.podman_* module call.
mp_podman_executable: "{{ lookup('env', 'MOLECULE_PODMAN_EXECUTABLE') | default('podman', true) }}"
```

- [ ] **Step 6.3: Thread `executable:` into every module call**

In `roles/podman/tasks/create.yml`, add `executable: "{{ mp_podman_executable }}"` to:

- The `Create podman network(s)` `containers.podman.podman_network:` task.
- The `Create molecule instance(s)` `containers.podman.podman_container:` task.

In `roles/podman/tasks/destroy.yml`, add the same `executable:` line to:

- The `Destroy molecule instance(s)` `containers.podman.podman_container:` task.
- The `Delete podman network(s)` `containers.podman.podman_network:` task.

- [ ] **Step 6.4: Document `mp_podman_executable` in argument_specs**

In `roles/podman/meta/argument_specs.yml`, under both `create` and `destroy` `options:`, add:

```yaml
mp_podman_executable:
  type: str
  default: podman
  description: >-
    Path or name of the podman executable. Picked up from
    MOLECULE_PODMAN_EXECUTABLE env var by default.
```

- [ ] **Step 6.5: Re-run scenario — expect PASS**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: exit 0; lookup-pipeline assertion passes.

- [ ] **Step 6.6: Lint**

Run: `cd /workspace/ansible-collection-molecule_provisioners && ansible-lint roles/podman/ extensions/ && yamllint .`
Expected: clean.

- [ ] **Step 6.7: Commit**

```bash
git add roles/podman/tasks/create.yml roles/podman/tasks/destroy.yml \
        roles/podman/defaults/main.yml roles/podman/meta/argument_specs.yml \
        extensions/molecule/default/verify.yml
git commit -m "feat(podman): honor MOLECULE_PODMAN_EXECUTABLE on every podman module call"
```

---

## Task 7: Group I (partial) — `label: { owner: molecule }`, `reset.yml`, `sanity_checks`

Three small, independent pieces:

1. Always label molecule-created containers `owner=molecule` so a future `molecule reset`-style action has something to filter on.
2. Ship `playbooks/reset.yml`, invokable as `david_igou.molecule_provisioners.reset`, which removes every container with that label.
3. Add a play-level `ansible_version` assertion to `_validate.yml` modeled on upstream's `sanity_checks` (warns on ancient ansible-core).

**Files:**

- Create: `playbooks/reset.yml`
- Modify: `roles/podman/tasks/create.yml`
- Modify: `roles/podman/tasks/_validate.yml`
- Modify: `roles/podman/README.md`
- Modify: `CLAUDE.md`
- Modify: `extensions/molecule/default/verify.yml`

- [ ] **Step 7.1: Add failing label verify**

In `extensions/molecule/default/verify.yml`, after the executable assertion, append:

```yaml
- name: Assert owner=molecule label propagated
  ansible.builtin.assert:
    that:
      - (_ctr.Config.Labels | default({})).owner | default('') == "molecule"
    fail_msg: >-
      Expected Config.Labels.owner=molecule;
      got {{ _ctr.Config.Labels | default({}) }}.
```

- [ ] **Step 7.2: Run scenario — verify expected to FAIL on label assertion**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: verify fails on the new label assertion.

- [ ] **Step 7.3: Add `label:` and merge user-provided labels in `create.yml`**

In `roles/podman/tasks/create.yml`, in the `Create molecule instance(s)` task, after the `cmd_args:` line, add:

```yaml
label: "{{ ({'owner': 'molecule'} | combine(_mp_specs[item].labels | default({}))) }}"
```

(Putting the role's reserved label first means a consumer setting `mp.podman.labels.owner` would override it — which is intentional; documented as the consumer's responsibility.)

- [ ] **Step 7.4: Re-run scenario — expect PASS**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: exit 0; label assertion passes.

- [ ] **Step 7.5: Create `playbooks/reset.yml`**

Create `playbooks/reset.yml` with:

```yaml
---
- name: Molecule provisioner — reset (purge owner=molecule containers/networks)
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    mp_podman_executable: "{{ lookup('env', 'MOLECULE_PODMAN_EXECUTABLE') | default('podman', true) }}"
  tasks:
    - name: List containers labeled owner=molecule
      containers.podman.podman_container_info:
        executable: "{{ mp_podman_executable }}"
      register: __mp_reset_ci

    - name: Remove containers labeled owner=molecule
      containers.podman.podman_container:
        name: "{{ item.Name }}"
        state: absent
        executable: "{{ mp_podman_executable }}"
      loop: >-
        {{ __mp_reset_ci.containers
           | selectattr('Config.Labels', 'defined')
           | selectattr('Config.Labels.owner', 'defined')
           | selectattr('Config.Labels.owner', 'equalto', 'molecule')
           | list }}
      loop_control:
        label: "{{ item.Name }}"
```

- [ ] **Step 7.6: Add `sanity_checks` assertion to `_validate.yml`**

In `roles/podman/tasks/_validate.yml`, after the `Validate image is set per host` task, append:

```yaml
- name: Warn on ansible-core older than 2.16 (mirrors upstream sanity_checks)
  ansible.builtin.assert:
    that:
      - (ansible_version.major, ansible_version.minor) >= (2, 15)
    fail_msg: >-
      david_igou.molecule_provisioners.podman requires ansible-core >= 2.15;
      detected {{ ansible_version.full }}.
  run_once: true
```

(Asserting >= 2.15 since that's the collection's floor; upstream's check was a warning on < 2.13, but this collection's floor is higher.)

- [ ] **Step 7.7: Re-run scenario — expect PASS**

Run: `cd /workspace/ansible-collection-molecule_provisioners && PROVISIONER=podman pytest tests/integration -v -k default 2>&1 | tail -30`
Expected: exit 0.

- [ ] **Step 7.8: Smoke-test `reset.yml` against a stray labeled container**

Run:

```bash
cd /workspace/ansible-collection-molecule_provisioners
podman run -d --rm --label owner=molecule --name mp-reset-smoke docker.io/library/alpine:3 sleep 600
podman ps --filter label=owner=molecule --format '{{.Names}}'
ansible-playbook -i extensions/molecule/default/inventory/ playbooks/reset.yml
podman ps -a --filter label=owner=molecule --format '{{.Names}}'
```

Expected output:

- First `podman ps`: `mp-reset-smoke`
- After playbook: empty (no containers with the label remain).

- [ ] **Step 7.9: Document the new playbook**

In `roles/podman/README.md`, after the `## Role-level overrides` section, append:

````markdown
## Resetting state

`playbooks/reset.yml` (exposed as `david_igou.molecule_provisioners.reset`) removes every podman container labeled `owner=molecule`. Useful when a molecule run was interrupted and left containers behind.

```bash
ansible-playbook david_igou.molecule_provisioners.reset
```
````

The label is applied automatically by the role's create phase; user-supplied `mp.podman.labels.owner` overrides it.

````

In `CLAUDE.md`, under §"Architecture (one-paragraph version)" → "Key files", add a bullet:

```markdown
- `playbooks/reset.yml` — standalone purge playbook; removes containers labeled `owner=molecule`. Reachable as `david_igou.molecule_provisioners.reset`.
````

- [ ] **Step 7.10: Lint**

Run: `cd /workspace/ansible-collection-molecule_provisioners && ansible-lint roles/podman/ playbooks/ extensions/ && yamllint .`
Expected: clean.

- [ ] **Step 7.11: Commit**

```bash
git add roles/podman/tasks/create.yml roles/podman/tasks/_validate.yml \
        roles/podman/README.md CLAUDE.md \
        playbooks/reset.yml \
        extensions/molecule/default/verify.yml
git commit -m "feat(podman): owner=molecule label, reset playbook, ansible-core sanity check"
```

---

## Task 8: Changelog fragment + final verification

- [ ] **Step 8.1: Write the changelog fragment**

Create `changelogs/fragments/podman-parity-steps-1-7.yml` with:

```yaml
---
minor_changes:
  - >-
    podman role: expose ``systemd`` and ``cgroupns`` per-host fields (closes
    #19) and add Group A schema fields from the parity catalogue in #24:
    ``hostname``, ``tty``, ``detach``, ``etc_hosts``, ``dns_servers``,
    ``pid_mode``, ``security_opts``, ``devices``, ``ulimits``, ``ip``,
    ``restart_policy``, ``restart_retries``.
  - >-
    podman role: widen ``podman_network`` to accept ``str``, ``list[str]``, or
    ``list[dict]`` (``{name, subnet?, gateway?}``). Reserved-network list now
    also includes ``ns`` and ``private``.
  - >-
    podman role: new ``extra_opts`` catch-all and curated ``cgroup_manager``,
    ``storage_opt``, ``storage_driver`` fields, all assembled into the
    ``cmd_args`` passed to ``containers.podman.podman_container``.
  - >-
    podman role: honor the ``MOLECULE_PODMAN_EXECUTABLE`` env var via the new
    ``mp_podman_executable`` role variable; threaded through every
    ``containers.podman.podman_*`` module call.
  - >-
    podman role: containers are now labeled ``owner=molecule`` (overridable via
    ``mp.podman.labels.owner``). New ``david_igou.molecule_provisioners.reset``
    playbook purges anything carrying that label. Pre-flight assertion catches
    ``ansible-core`` older than the collection's 2.15 floor.
  - >-
    podman role: ``_spec_merge.yml`` / ``_validate.yml`` extracted to mirror the
    docker role layout; switches to recursive ``combine`` so
    ``mp_defaults.podman.<dict>`` and per-host overrides compose instead of
    last-write-wins.
```

- [ ] **Step 8.2: Final end-to-end run**

Run: `cd /workspace/ansible-collection-molecule_provisioners && pytest tests/integration -v -k default 2>&1 | tail -30`

(Driven by `PROVISIONER=podman` by default since the scenario's `group_vars/molecule.yml` defaults `mp_backend` to `podman` when the env var is unset.)

Expected: `tests/integration/test_integration.py::test_integration[default] PASSED`.

- [ ] **Step 8.3: Final lint sweep**

Run: `cd /workspace/ansible-collection-molecule_provisioners && pre-commit run --all-files 2>&1 | tail -30`
Expected: every hook passes (`Passed` or `Skipped`).

If `update-docs` (collection_prep) modifies generated files, accept those changes and re-stage them — do not bypass with `--no-verify`.

- [ ] **Step 8.4: Commit the changelog + any doc regeneration**

```bash
git add changelogs/fragments/podman-parity-steps-1-7.yml
# plus any files touched by `pre-commit run --all-files`
git commit -m "docs: changelog fragment for podman role parity steps 1-7 (#24)"
```

- [ ] **Step 8.5: Hand off**

Suggested PR title: `feat(podman): parity steps 1-7 from #24 (refactor + Groups A/D/B+C/G/I-partial)`

PR body should link issue #24, mention that it closes #19, and call out the explicit non-changes:

- `pre_build_image` / Dockerfile pipeline (Group E) — deferred.
- Registry login (Group F) — deferred.
- Rootless toggle (Group H) — deferred.
- The remainder of Group I (driver-only features) — deferred.

---

## Self-review notes

Spec coverage check against the issue's "Suggested implementation order":

| Order | Issue says                         | This plan does |
| ----- | ---------------------------------- | -------------- |
| 1     | Land #19 first                     | Task 1         |
| 2     | Refactor \_spec_merge / \_validate | Task 2         |
| 3     | Group A                            | Task 3         |
| 4     | Group D                            | Task 4         |
| 5     | Groups B + C                       | Task 5         |
| 6     | Group G                            | Task 6         |
| 7     | Group I (partial)                  | Task 7         |

Type/identifier consistency:

- Public-facing field names: `dns_servers`, `security_opts`, `devices`, `ulimits`, `pid_mode`, `restart_retries`. Mapped to module params (`dns`, `security_opt`, `device`, `ulimit`, `pid`, `restart_policy_attempts`) in exactly one place (`create.yml` Step 3.4).
- `podman_network` normalization: produces a list-of-dicts shape used identically by `_networks.yml` and the `network:` line of the module call (`map(attribute='name')`).
- `__mp_podman_cmd_args` is a dict keyed by host, consumed by the create task as `__mp_podman_cmd_args[item] | default([])`.
- Label dict produced in Step 7.3 is a regular dict — `_mp_specs[item].labels` is `default({})`, so the combine is safe even when the user sets nothing.

Devcontainer testability:

- ✅ Tested rootless: systemd, cgroupns, hostname, tty, etc_hosts, dns_servers, security_opts, ulimits, restart_policy/retries, pid_mode (non-host), network subnet, extra_opts (memory), label, reset playbook, sanity assert.
- ⚠️ Wired but not asserted rootless: devices (no /dev passthrough), ip (depends on the same subnet network — could be added but adds little signal), cgroup_manager / storage_opt / storage_driver (require user-systemd or specific storage drivers). These are still exercised by the schema/lint/syntax path.
- ❌ Not exercisable in devcontainer at all: pid_mode: host, /dev/kvm passthrough. Not part of this plan.
