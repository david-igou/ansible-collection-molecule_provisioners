# docker Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `docker` backend to `david_igou.molecule_provisioners` that mirrors the `podman` role's shape (local daemon, async parallel container create, network lifecycle, runtime inventory writeback) using `community.docker.docker_container` + `community.docker.docker_network`. Omits image build, registry login, and remote daemons.

**Architecture:** New `roles/docker/` parallel to `roles/podman/`. The top-level dispatcher already handles new backends generically — only `playbooks/group_vars/all.yml` needs `docker` added to `mp_supported_backends`. Inside the role: `_spec_merge.yml` (3-level merge), `_validate.yml` (image-set assert), `_networks.yml` (distinct-network collection shared by create + destroy), and the lifecycle files `create.yml`/`destroy.yml`/`prepare.yml`. Module-first throughout — no shell-outs.

**Tech Stack:** Ansible 2.15+, `community.docker: >=4.0.0` (new dep). Host-tool prereqs: a reachable local docker daemon and `community.docker.docker` connection plugin. Test fixtures under `tests/integration/docker/` for fast unit-style assertion playbooks; the molecule self-test scenario at `extensions/molecule/default/` (extended with an `mp.docker` block on the existing `instance` host) is the end-to-end gate.

**Spec reference:** [`docs/superpowers/specs/2026-05-22-docker-backend-design.md`](../specs/2026-05-22-docker-backend-design.md)

---

## File Map

**Create:**
- `roles/docker/defaults/main.yml`
- `roles/docker/meta/main.yml`
- `roles/docker/meta/argument_specs.yml`
- `roles/docker/README.md`
- `roles/docker/tasks/main.yml`
- `roles/docker/tasks/create.yml`
- `roles/docker/tasks/destroy.yml`
- `roles/docker/tasks/prepare.yml`
- `roles/docker/tasks/_spec_merge.yml`
- `roles/docker/tasks/_validate.yml`
- `roles/docker/tasks/_networks.yml`
- `tests/integration/docker/__init__.py`
- `tests/integration/docker/test_docker_unit.py`
- `tests/integration/docker/fixtures/valid_minimal.yml`
- `tests/integration/docker/fixtures/missing_image.yml`
- `tests/integration/docker/fixtures/networks_and_reserved.yml`
- `tests/integration/docker/assertions/run_validate.yml`
- `tests/integration/docker/assertions/run_networks.yml`
- `tests/integration/docker/assertions/run_destroy.yml`

**Modify:**
- `playbooks/group_vars/all.yml` — add `docker` to `mp_supported_backends`
- `galaxy.yml` — add `community.docker: ">=4.0.0"` dependency; add `docker` to tags; update description
- `extensions/molecule/default/inventory/hosts.yml` — add `mp.docker` block on `instance`
- `extensions/molecule/default/inventory/group_vars/molecule.yml` — add `mp_defaults.docker`
- `docs/examples/inventory/hosts.yml` — add `mp.docker` example
- `docs/examples/inventory/group_vars/molecule.yml` — add `mp_defaults.docker`
- `CLAUDE.md` — update architecture paragraph + public-contract schema to mention docker
- `.github/workflows/tests.yml` — add `integration-docker` job; add to `all_green` needs

---

## Phase 1 — Devcontainer precheck

### Task 0: Verify docker is reachable in this devcontainer

This gates the whole plan. The spec explicitly says: if a feature is untestable in the devcontainer it gets descoped, and "no docker daemon reachable" means the role itself can't be exercised here — at which point the implementer should pause and ask the user how to proceed (e.g. fall back to CI-only verification, or skip the role entirely).

**Files:** none (precheck only)

- [ ] **Step 1: Probe the docker socket**

Run: `docker info 2>&1 | head -20`

Expected: a "Server Version:" line and no "Cannot connect to the Docker daemon" error. If the command fails, do not proceed past this task — surface the failure to the user and stop.

- [ ] **Step 2: Confirm `community.docker` is installable**

Run: `ansible-galaxy collection install community.docker:>=4.0.0 --upgrade`

Expected: "Collection 'community.docker:...' was installed successfully" or "already installed".

- [ ] **Step 3: Confirm the connection plugin loads**

Run: `ansible-doc -t connection community.docker.docker 2>&1 | head -5`

Expected: a doc header for the docker connection plugin, no traceback.

- [ ] **Step 4: Smoke-test a single container round-trip**

Run:
```bash
docker run --rm --name mp-docker-smoke -d alpine:3.20 sleep 30 \
  && docker exec mp-docker-smoke true \
  && docker rm -f mp-docker-smoke \
  && echo OK
```

Expected: ends with `OK`. If this fails (rootless permission, no internet, cgroup issue), stop and surface to the user.

---

## Phase 2 — Wire the dispatcher

### Task 1: Add docker to the backend allow-list

**Files:**
- Modify: `playbooks/group_vars/all.yml`

- [ ] **Step 1: Read the current allow-list**

Run: `cat playbooks/group_vars/all.yml`

Expected:
```yaml
---
# Loaded by every dispatcher play in playbooks/.
mp_supported_backends:
  - podman
  - kubevirt
  - qemu
```

- [ ] **Step 2: Append `docker`**

Edit `playbooks/group_vars/all.yml` to read:
```yaml
---
# Loaded by every dispatcher play in playbooks/.
mp_supported_backends:
  - podman
  - kubevirt
  - qemu
  - docker
```

- [ ] **Step 3: Verify the dispatcher accepts `docker` (it'll still fail later because the role doesn't exist yet, but the allow-list check must pass)**

Run:
```bash
ANSIBLE_COLLECTIONS_PATH="$HOME/.ansible/collections" \
  ansible-playbook playbooks/create.yml \
  -i extensions/molecule/default/inventory/ \
  -e mp_backend=docker 2>&1 | head -30
```

Expected: passes the "Validate backend" assert. It will likely fail later with "ERROR! the role 'david_igou.molecule_provisioners.docker' was not found" — that's correct for this checkpoint; only the allow-list step needs to pass.

- [ ] **Step 4: Commit**

```bash
git add playbooks/group_vars/all.yml
git commit -m "feat(docker): register backend in dispatcher allow-list"
```

---

## Phase 3 — Role skeleton

### Task 2: Scaffold the role layout (entry point + defaults)

**Files:**
- Create: `roles/docker/tasks/main.yml`
- Create: `roles/docker/defaults/main.yml`
- Create: `roles/docker/meta/main.yml`

- [ ] **Step 1: Create the directory structure**

Run:
```bash
mkdir -p roles/docker/{tasks,defaults,meta}
```

- [ ] **Step 2: Write the default entry point (refuses direct invocation)**

Create `roles/docker/tasks/main.yml`:
```yaml
---
- name: "Docker role: no default entry point"
  ansible.builtin.fail:
    msg: >-
      david_igou.molecule_provisioners.docker has no main entry point.
      Use tasks_from=create|destroy|prepare via include_role.
```

- [ ] **Step 3: Write `defaults/main.yml`**

Create `roles/docker/defaults/main.yml`:
```yaml
---
# Defaults for david_igou.molecule_provisioners.docker.
# Per-platform values come from hostvars[item].mp.docker.*

# Reserved network names — never (re)created/deleted as standalone networks.
mp_docker_reserved_networks:
  - bridge
  - host
  - none

# Async timing for create/destroy bulk operations.
# retries * delay should equal (or exceed) async_timeout; otherwise
# async_status can give up while the underlying job is still healthy.
mp_docker_async_timeout: 7200
mp_docker_async_retries: 300
mp_docker_async_delay: 24

# Per-host field defaults. Layered as: this dict <- mp_defaults.docker <- hostvars[item].mp.docker.
# Only `image` is required. Fields whose role default is "omit" (everything not listed
# here) are intentionally absent — the role's module calls use `| default(omit)` so the
# docker_container module's own defaults apply.
mp_docker_role_defaults:
  command_handling: compatibility
  override_command: true
  privileged: false
  capabilities: []
  volumes: []
  env: {}
  labels: {}
  networks_cli_compatible: true
  force_kill: true
  keep_volumes: true
```

- [ ] **Step 4: Write `meta/main.yml`**

Create `roles/docker/meta/main.yml`:
```yaml
---
galaxy_info:
  role_name: docker
  author: David Igou
  description: Molecule provisioner role using community.docker
  company: ""
  license: GPL-3.0-or-later
  min_ansible_version: "2.15"
  platforms:
    - name: GenericLinux
      versions: [all]
  galaxy_tags:
    - molecule
    - docker
    - testing
dependencies: []
```

- [ ] **Step 5: Verify lint passes on the skeleton**

Run: `ansible-lint roles/docker/`

Expected: zero errors (warnings about empty roles are acceptable; the directories will fill out in later tasks).

- [ ] **Step 6: Commit**

```bash
git add roles/docker/
git commit -m "feat(docker): scaffold role layout with defaults and entry point"
```

---

## Phase 4 — Spec merge + validation (TDD)

### Task 3: Write failing tests for spec merge and validation

**Files:**
- Create: `tests/integration/docker/__init__.py`
- Create: `tests/integration/docker/test_docker_unit.py`
- Create: `tests/integration/docker/fixtures/valid_minimal.yml`
- Create: `tests/integration/docker/fixtures/missing_image.yml`
- Create: `tests/integration/docker/assertions/run_validate.yml`

- [ ] **Step 1: Create the directory structure**

Run:
```bash
mkdir -p tests/integration/docker/{fixtures,assertions}
touch tests/integration/docker/__init__.py
```

- [ ] **Step 2: Write the minimal-valid fixture**

Create `tests/integration/docker/fixtures/valid_minimal.yml`:
```yaml
---
all:
  children:
    molecule:
      hosts:
        h-minimal:
          mp:
            docker:
              image: quay.io/centos/centos:stream9
        h-overrides:
          mp:
            docker:
              image: docker.io/library/alpine:3.20
              privileged: true
              volumes:
                - /tmp:/tmp:rw
  vars:
    mp_backend: docker
    mp_defaults:
      docker:
        command: /sbin/init
```

- [ ] **Step 3: Write the missing-image fixture**

Create `tests/integration/docker/fixtures/missing_image.yml`:
```yaml
---
all:
  children:
    molecule:
      hosts:
        h-broken:
          mp:
            docker:
              # image intentionally omitted
              privileged: true
  vars:
    mp_backend: docker
```

- [ ] **Step 4: Write the validate-runner playbook**

Create `tests/integration/docker/assertions/run_validate.yml`:
```yaml
---
- name: Exercise spec-merge + validation
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Include role defaults
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../../roles/docker/defaults/main.yml"
    - name: Surface mp_defaults from the molecule group
      ansible.builtin.set_fact:
        mp_defaults: "{{ hostvars[groups['molecule'][0]].mp_defaults | default({}) }}"
    - name: Run spec merge
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/docker/tasks/_spec_merge.yml"
    - name: Run validation
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/docker/tasks/_validate.yml"
    - name: Assert merge picked up mp_defaults
      ansible.builtin.assert:
        that:
          - _mp_specs['h-minimal'].image == 'quay.io/centos/centos:stream9'
          - _mp_specs['h-minimal'].command == '/sbin/init'
          - _mp_specs['h-minimal'].privileged == false
        fail_msg: "Spec merge did not layer mp_defaults onto role_defaults correctly."
      when: groups['molecule'] | length == 2  # only the valid_minimal fixture has h-minimal
```

- [ ] **Step 5: Write the pytest module**

Create `tests/integration/docker/test_docker_unit.py`:
```python
"""Fast, daemon-less tests for the docker role's validation and merge logic."""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
ASSERTIONS = HERE / "assertions"
COLLECTION_ROOT = HERE.parent.parent.parent  # ansible_collections/.../molecule_provisioners


def _run(playbook: str, inventory: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / inventory), str(ASSERTIONS / playbook)],
        cwd=COLLECTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_minimal_passes_validation() -> None:
    proc = _run("run_validate.yml", "valid_minimal.yml")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_missing_image_fails_with_message() -> None:
    proc = _run("run_validate.yml", "missing_image.yml")
    assert proc.returncode != 0
    assert "is missing docker.image" in proc.stdout
```

- [ ] **Step 6: Run the tests, expect failure**

Run: `pytest tests/integration/docker/test_docker_unit.py -v`

Expected: both tests FAIL. The "Include tasks" step will error because `roles/docker/tasks/_spec_merge.yml` and `_validate.yml` don't exist yet. That's the failing state we want.

- [ ] **Step 7: Commit (red state)**

```bash
git add tests/integration/docker/
git commit -m "test(docker): add failing tests for spec merge and validation"
```

### Task 4: Implement spec merge

**Files:**
- Create: `roles/docker/tasks/_spec_merge.yml`

- [ ] **Step 1: Write `_spec_merge.yml`**

Create `roles/docker/tasks/_spec_merge.yml`:
```yaml
---
# Build _mp_specs[host] from three layers:
#   role defaults <- mp_defaults.docker <- hostvars[item].mp.docker
# Defensive on every layer so destroy can still merge for half-failed creates.
- name: Initialize docker spec map
  ansible.builtin.set_fact:
    _mp_specs: {}

- name: Merge per-host specs with mp_defaults and role defaults
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: mp_docker_role_defaults
                 | combine(mp_defaults['docker'] | default({}), recursive=True)
                 | combine((hostvars[item].mp | default({}))['docker'] | default({}), recursive=True)
         }, recursive=True) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 2: Test runs still fail (validation file missing)**

Run: `pytest tests/integration/docker/test_docker_unit.py::test_valid_minimal_passes_validation -v`

Expected: still FAILS — error now points at the missing `_validate.yml`. This confirms `_spec_merge.yml` loads without error.

### Task 5: Implement validation

**Files:**
- Create: `roles/docker/tasks/_validate.yml`

- [ ] **Step 1: Write `_validate.yml`**

Create `roles/docker/tasks/_validate.yml`:
```yaml
---
# Fail-fast validation. Run after _spec_merge.yml. No side effects.
- name: Validate image is set per host
  ansible.builtin.assert:
    that:
      - _mp_specs[item].image is defined
      - (_mp_specs[item].image | string | length) > 0
    fail_msg: >-
      Host '{{ item }}' is missing docker.image. Set
      hostvars.{{ item }}.mp.docker.image to a fully-qualified image reference.
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 2: Run both unit tests, expect green**

Run: `pytest tests/integration/docker/test_docker_unit.py -v`

Expected: both `test_valid_minimal_passes_validation` and `test_missing_image_fails_with_message` PASS.

- [ ] **Step 3: Commit**

```bash
git add roles/docker/tasks/_spec_merge.yml roles/docker/tasks/_validate.yml
git commit -m "feat(docker): spec merge + image-set validation"
```

---

## Phase 5 — Networks

### Task 6: Write failing test for the network collector

**Files:**
- Create: `tests/integration/docker/fixtures/networks_and_reserved.yml`
- Create: `tests/integration/docker/assertions/run_networks.yml`
- Modify: `tests/integration/docker/test_docker_unit.py`

- [ ] **Step 1: Write the fixture (two distinct networks + one reserved name)**

Create `tests/integration/docker/fixtures/networks_and_reserved.yml`:
```yaml
---
all:
  children:
    molecule:
      hosts:
        h-a:
          mp:
            docker:
              image: docker.io/library/alpine:3.20
              networks:
                - name: mp-net-a
                - name: bridge   # reserved; must be filtered
        h-b:
          mp:
            docker:
              image: docker.io/library/alpine:3.20
              networks:
                - name: mp-net-a   # duplicate; must be unique-ified
                - name: mp-net-b
  vars:
    mp_backend: docker
```

- [ ] **Step 2: Write the network-runner playbook**

Create `tests/integration/docker/assertions/run_networks.yml`:
```yaml
---
- name: Exercise the network collector
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Include role defaults
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../../roles/docker/defaults/main.yml"
    - name: Run spec merge
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/docker/tasks/_spec_merge.yml"
    - name: Run network collector
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/docker/tasks/_networks.yml"
    - name: Assert distinct non-reserved networks are collected
      ansible.builtin.assert:
        that:
          - (__mp_docker_networks | unique | sort) == ['mp-net-a', 'mp-net-b']
        fail_msg: "Collector returned {{ __mp_docker_networks | default('UNDEFINED') }}"
```

- [ ] **Step 3: Add the test case to the pytest module**

Edit `tests/integration/docker/test_docker_unit.py`, appending:
```python


def test_networks_collector_dedupes_and_skips_reserved() -> None:
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "networks_and_reserved.yml"),
         str(ASSERTIONS / "run_networks.yml")],
        cwd=COLLECTION_ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 4: Run it, expect failure**

Run: `pytest tests/integration/docker/test_docker_unit.py::test_networks_collector_dedupes_and_skips_reserved -v`

Expected: FAIL — `_networks.yml` doesn't exist.

### Task 7: Implement `_networks.yml`

**Files:**
- Create: `roles/docker/tasks/_networks.yml`

- [ ] **Step 1: Write the collector**

Create `roles/docker/tasks/_networks.yml`:
```yaml
---
# Build __mp_docker_networks: the deduped list of non-reserved network names
# referenced by any host in groups['molecule']. Shared by create.yml + destroy.yml.
- name: Initialize docker network list
  ansible.builtin.set_fact:
    __mp_docker_networks: []

- name: Collect network names from per-host specs
  ansible.builtin.set_fact:
    __mp_docker_networks: >-
      {{ __mp_docker_networks +
         (_mp_specs[item].networks | map(attribute='name') | list) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  when:
    - _mp_specs[item].networks is defined
    - _mp_specs[item].networks is sequence

- name: Deduplicate and drop reserved names
  ansible.builtin.set_fact:
    __mp_docker_networks: >-
      {{ __mp_docker_networks
         | unique
         | difference(mp_docker_reserved_networks) }}
```

- [ ] **Step 2: Run the test, expect pass**

Run: `pytest tests/integration/docker/test_docker_unit.py::test_networks_collector_dedupes_and_skips_reserved -v`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add roles/docker/tasks/_networks.yml \
        tests/integration/docker/fixtures/networks_and_reserved.yml \
        tests/integration/docker/assertions/run_networks.yml \
        tests/integration/docker/test_docker_unit.py
git commit -m "feat(docker): network collector with dedup and reserved-name filter"
```

---

## Phase 6 — Create lifecycle

### Task 8: Implement `create.yml`

**Files:**
- Create: `roles/docker/tasks/create.yml`

This task has no unit-test red/green — the E2E molecule run in Phase 9 is the verification step. The file is mostly explicit option forwarding, mirroring podman's create.yml.

- [ ] **Step 1: Write `create.yml`**

Create `roles/docker/tasks/create.yml`:
```yaml
---
- name: Merge per-host specs
  ansible.builtin.include_tasks: _spec_merge.yml

- name: Validate per-host specs
  ansible.builtin.include_tasks: _validate.yml

- name: Build docker network list
  ansible.builtin.include_tasks: _networks.yml

- name: Create docker network(s)
  community.docker.docker_network:
    name: "{{ item }}"
    state: present
  loop: "{{ __mp_docker_networks | default([]) }}"

# Match molecule-plugins behavior: when override_command is true and no command is
# set, run a long-lived keepalive so the container stays up for connection tests.
- name: Determine the command directives
  ansible.builtin.set_fact:
    _mp_docker_command_directives: >-
      {{ _mp_docker_command_directives | default({})
         | combine({
             item: (_mp_specs[item].command
                    | default('bash -c "while true; do sleep 10000; done"'))
           }) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  when:
    - _mp_specs[item].override_command | default(true)

# Launch all containers simultaneously (poll: 0), then wait for each below.
# A failure during the wait leaves still-running async jobs in flight; run
# destroy to clean up partially-created instances.
- name: Create molecule instance(s)
  community.docker.docker_container:
    name: "{{ item }}"
    image: "{{ _mp_specs[item].image }}"
    state: started
    recreate: false
    hostname: "{{ _mp_specs[item].hostname | default(item) }}"
    command: "{{ (_mp_docker_command_directives | default({}))[item] | default(omit) }}"
    command_handling: "{{ _mp_specs[item].command_handling | default('compatibility') }}"
    privileged: "{{ _mp_specs[item].privileged | default(false) }}"
    user: "{{ _mp_specs[item].user | default(omit) }}"
    tty: "{{ _mp_specs[item].tty | default(omit) }}"
    pid_mode: "{{ _mp_specs[item].pid_mode | default(omit) }}"
    cgroupns_mode: "{{ _mp_specs[item].cgroupns_mode | default(omit) }}"
    runtime: "{{ _mp_specs[item].runtime | default(omit) }}"
    platform: "{{ _mp_specs[item].platform | default(omit) }}"
    capabilities: "{{ _mp_specs[item].capabilities | default(omit) }}"
    security_opts: "{{ _mp_specs[item].security_opts | default(omit) }}"
    sysctls: "{{ _mp_specs[item].sysctls | default(omit) }}"
    ulimits: "{{ _mp_specs[item].ulimits | default(omit) }}"
    devices: "{{ _mp_specs[item].devices | default(omit) }}"
    volumes: "{{ _mp_specs[item].volumes | default(omit) }}"
    mounts: "{{ _mp_specs[item].mounts | default(omit) }}"
    tmpfs: "{{ _mp_specs[item].tmpfs | default(omit) }}"
    shm_size: "{{ _mp_specs[item].shm_size | default(omit) }}"
    networks: "{{ _mp_specs[item].networks | default(omit) }}"
    network_mode: "{{ _mp_specs[item].network_mode | default(omit) }}"
    networks_cli_compatible: "{{ _mp_specs[item].networks_cli_compatible | default(true) }}"
    purge_networks: "{{ _mp_specs[item].purge_networks | default(omit) }}"
    dns_servers: "{{ _mp_specs[item].dns_servers | default(omit) }}"
    etc_hosts: "{{ _mp_specs[item].etc_hosts | default(omit) }}"
    exposed_ports: "{{ _mp_specs[item].exposed_ports | default(omit) }}"
    published_ports: "{{ _mp_specs[item].published_ports | default(omit) }}"
    links: "{{ _mp_specs[item].links | default(omit) }}"
    env: "{{ _mp_specs[item].env | default({}) }}"
    labels: "{{ _mp_specs[item].labels | default({}) }}"
    restart_policy: "{{ _mp_specs[item].restart_policy | default(omit) }}"
    restart_retries: "{{ _mp_specs[item].restart_retries | default(omit) }}"
    stop_signal: "{{ _mp_specs[item].stop_signal | default(omit) }}"
    kill_signal: "{{ _mp_specs[item].kill_signal | default(omit) }}"
    memory: "{{ _mp_specs[item].memory | default(omit) }}"
    memory_swap: "{{ _mp_specs[item].memory_swap | default(omit) }}"
  register: __mp_docker_create
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  async: "{{ mp_docker_async_timeout }}"
  poll: 0

- name: Wait for instance(s) creation to complete
  ansible.builtin.async_status:
    jid: "{{ item.ansible_job_id }}"
  register: __mp_docker_jobs
  until: __mp_docker_jobs.finished
  retries: "{{ mp_docker_async_retries }}"
  delay: "{{ mp_docker_async_delay }}"
  loop: "{{ __mp_docker_create.results }}"
  loop_control:
    label: "{{ item.item }}"

# Augment the static inventory with ansible_connection so subsequent molecule
# phases (prepare/converge/verify) talk to the containers via the docker
# connection plugin. Each phase is a separate ansible-playbook invocation, so
# the augmentation has to be written to disk in molecule_ephemeral_directory/
# inventory/ (which is in molecule's inventory chain).
- name: Build runtime connection dict
  ansible.builtin.set_fact:
    __mp_docker_runtime_hosts: >-
      {{ __mp_docker_runtime_hosts | default({})
         | combine({item: {'ansible_connection': 'community.docker.docker'}}) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Write runtime connection inventory file
  vars:
    runtime_inventory:
      all:
        hosts: "{{ __mp_docker_runtime_hosts }}"
  ansible.builtin.copy:
    content: "{{ runtime_inventory | to_nice_yaml }}"
    dest: "{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml"
    mode: "0600"

- name: Refresh inventory
  ansible.builtin.meta: refresh_inventory
```

- [ ] **Step 2: Lint pass**

Run: `ansible-lint roles/docker/tasks/create.yml`

Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add roles/docker/tasks/create.yml
git commit -m "feat(docker): create.yml with async container launch + runtime inventory"
```

---

## Phase 7 — Destroy lifecycle (TDD)

### Task 9: Write failing destroy-idempotency test

**Files:**
- Create: `tests/integration/docker/assertions/run_destroy.yml`
- Modify: `tests/integration/docker/test_docker_unit.py`

- [ ] **Step 1: Write the destroy-runner playbook**

Create `tests/integration/docker/assertions/run_destroy.yml`:
```yaml
---
# Verify destroy.yml is idempotent on a fresh (never-created) state.
- name: Exercise destroy on never-created hosts
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    molecule_ephemeral_directory: "{{ lookup('env', 'MOLECULE_EPHEMERAL_DIRECTORY')
                                       | default('/tmp/molecule-fake-ephemeral', true) }}"
  tasks:
    - name: Include role defaults
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../../roles/docker/defaults/main.yml"
    - name: Surface mp_defaults
      ansible.builtin.set_fact:
        mp_defaults: "{{ hostvars[groups['molecule'][0]].mp_defaults | default({}) }}"
    - name: Run destroy entrypoint
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/docker/tasks/destroy.yml"
```

- [ ] **Step 2: Add the test case**

Edit `tests/integration/docker/test_docker_unit.py`, appending:
```python


def test_destroy_is_idempotent_on_fresh_state(tmp_path) -> None:
    import os
    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "valid_minimal.yml"),
         str(ASSERTIONS / "run_destroy.yml")],
        cwd=COLLECTION_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 3: Run it, expect failure**

Run: `pytest tests/integration/docker/test_docker_unit.py::test_destroy_is_idempotent_on_fresh_state -v`

Expected: FAIL — `roles/docker/tasks/destroy.yml` doesn't exist.

### Task 10: Implement `destroy.yml`

**Files:**
- Create: `roles/docker/tasks/destroy.yml`

- [ ] **Step 1: Write `destroy.yml`**

Create `roles/docker/tasks/destroy.yml`:
```yaml
---
# Defensive merge — a host may not have an mp.docker block on destroy if the
# consumer added it after a partial create, and destroy must remain idempotent.
- name: Merge per-host specs (defensive)
  ansible.builtin.include_tasks: _spec_merge.yml

- name: Destroy molecule instance(s)
  community.docker.docker_container:
    name: "{{ item }}"
    state: absent
    force_kill: "{{ _mp_specs[item].force_kill | default(true) }}"
    keep_volumes: "{{ _mp_specs[item].keep_volumes | default(true) }}"
  register: __mp_docker_destroy
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  async: "{{ mp_docker_async_timeout }}"
  poll: 0

- name: Wait for instance(s) deletion to complete
  ansible.builtin.async_status:
    jid: "{{ item.ansible_job_id }}"
  register: __mp_docker_jobs
  until: __mp_docker_jobs.finished
  retries: "{{ mp_docker_async_retries }}"
  delay: "{{ mp_docker_async_delay }}"
  loop: "{{ __mp_docker_destroy.results }}"
  loop_control:
    label: "{{ item.item }}"

- name: Build docker network list
  ansible.builtin.include_tasks: _networks.yml

- name: Delete docker network(s)
  community.docker.docker_network:
    name: "{{ item }}"
    state: absent
    force: true
  loop: "{{ __mp_docker_networks | default([]) }}"
  failed_when: false
```

- [ ] **Step 2: Run the test, expect pass**

Run: `pytest tests/integration/docker/test_docker_unit.py::test_destroy_is_idempotent_on_fresh_state -v`

Expected: PASS — destroy against a never-created scenario is a no-op because `docker_container state=absent` succeeds when the container doesn't exist, and `docker_network state=absent` with `failed_when: false` swallows missing-network errors.

- [ ] **Step 3: Commit**

```bash
git add roles/docker/tasks/destroy.yml \
        tests/integration/docker/assertions/run_destroy.yml \
        tests/integration/docker/test_docker_unit.py
git commit -m "feat(docker): destroy.yml with async parallel teardown + network cleanup"
```

---

## Phase 8 — Prepare

### Task 11: Implement `prepare.yml`

**Files:**
- Create: `roles/docker/tasks/prepare.yml`

- [ ] **Step 1: Write `prepare.yml`**

Looking at the podman role's `prepare.yml`, it installs `sudo` inside each container. The docker role uses the same `community.docker.docker` connection plugin and the same expectation of an init-capable image, so we mirror it:

Create `roles/docker/tasks/prepare.yml`:
```yaml
---
- name: Ensure sudo is installed
  ansible.builtin.package:
    name: sudo
    state: present
```

- [ ] **Step 2: Lint pass**

Run: `ansible-lint roles/docker/tasks/prepare.yml`

Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add roles/docker/tasks/prepare.yml
git commit -m "feat(docker): prepare.yml installs sudo in each container"
```

---

## Phase 9 — End-to-end via the self-test scenario

### Task 12: Add docker block to the default scenario

**Files:**
- Modify: `extensions/molecule/default/inventory/hosts.yml`
- Modify: `extensions/molecule/default/inventory/group_vars/molecule.yml`

- [ ] **Step 1: Add `mp.docker` to the `instance` host**

Edit `extensions/molecule/default/inventory/hosts.yml`:
```yaml
---
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            podman:
              image: docker.io/geerlingguy/docker-fedora41-ansible:latest
            kubevirt:
              image: quay.io/kubevirt/fedora-cloud-container-disk-demo:latest
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              image_checksum: "sha256:6e7016f2c9f4d3c00f48789eb6b9043ba2172ccc1b6b1eaf3ed1e29dd3e52bb3"
            docker:
              image: docker.io/geerlingguy/docker-fedora41-ansible:latest
```

(Reusing the same `geerlingguy/docker-fedora41-ansible` image as podman keeps the converge/verify playbooks fully backend-agnostic and avoids a second image pull on the runner.)

- [ ] **Step 2: Add `mp_defaults.docker`**

Edit `extensions/molecule/default/inventory/group_vars/molecule.yml`, appending under `mp_defaults:`:
```yaml
  docker:
    command: /sbin/init
    privileged: true
```

Final file should look like:
```yaml
---
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"
mp_kubevirt_wait_timeout: 300
mp_qemu_wait_timeout: 300

mp_defaults:
  podman:
    command: /sbin/init
    privileged: true
  kubevirt:
    namespace: "{{ lookup('env', 'MOLECULE_NAMESPACE') | default('molecule', true) }}"
    memory: 1Gi
    ssh_user: cloud-user
  qemu:
    cpus: 2
    memory: 1024
    ssh_user: ubuntu
  docker:
    command: /sbin/init
    privileged: true
```

- [ ] **Step 3: Run the molecule scenario locally under `PROVISIONER=docker`**

Run:
```bash
cd /home/igou/ansible-collection-molecule_provisioners
PROVISIONER=docker ANSIBLE_COLLECTIONS_PATH="$HOME/.ansible/collections" \
  pytest tests/integration -v -k default -s -o addopts=""
```

Expected: molecule executes `dependency → syntax → create → prepare → converge → verify → destroy` and exits 0. `PLAY RECAP` shows the molecule host(s) reached.

If this fails, surface the failure to the user — per the descope policy, a feature that can't run here doesn't ship. Re-scope before continuing.

- [ ] **Step 4: Commit**

```bash
git add extensions/molecule/default/inventory/hosts.yml \
        extensions/molecule/default/inventory/group_vars/molecule.yml
git commit -m "test(docker): wire docker backend into the default self-test scenario"
```

---

## Phase 10 — CI

### Task 13: Add `integration-docker` job and update `all_green`

**Files:**
- Modify: `.github/workflows/tests.yml`

- [ ] **Step 1: Add the job (after `integration-qemu`, before `all_green`)**

In `.github/workflows/tests.yml`, insert this job after the `integration-qemu` block:
```yaml
  integration-docker:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Checkout into the canonical collection path
        uses: actions/checkout@v4
        with:
          path: ansible_collections/david_igou/molecule_provisioners

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install ansible + molecule + pytest plumbing
        working-directory: ansible_collections/david_igou/molecule_provisioners
        run: |
          python -m pip install --upgrade pip
          pip install ansible-core molecule \
                      pytest pytest-ansible pytest-xdist docker

      - name: Install collection dependencies
        working-directory: ansible_collections/david_igou/molecule_provisioners
        run: |
          ansible-galaxy collection install \
            containers.podman kubernetes.core community.crypto community.docker

      - name: Run docker integration tests
        working-directory: ansible_collections/david_igou/molecule_provisioners
        env:
          PROVISIONER: docker
          ANSIBLE_COLLECTIONS_PATH: ${{ github.workspace }}
        run: pytest tests/integration -v -k default
```

(GitHub-hosted `ubuntu-latest` runners ship a running docker daemon — no `sudo systemctl start docker` or socket-permission dance required. The `docker` pip package is needed by `community.docker` modules for the API client.)

- [ ] **Step 2: Update `all_green` to depend on the new job**

In the same file, edit the `all_green` block:
```yaml
  all_green:
    if: ${{ always() }}
    needs:
      - changelog
      - build-import
      - sanity
      - unit-galaxy
      - unit-source
      - ansible-lint
      - integration-podman
      - integration-kubevirt
      - integration-qemu
      - integration-docker
    runs-on: ubuntu-latest
    steps:
      - run: >-
          python -c "assert 'failure' not in
          set([
          '${{ needs.changelog.result }}',
          '${{ needs.sanity.result }}',
          '${{ needs.unit-galaxy.result }}',
          '${{ needs.ansible-lint.result }}',
          '${{ needs.unit-source.result }}',
          '${{ needs.integration-podman.result }}',
          '${{ needs.integration-kubevirt.result }}',
          '${{ needs.integration-qemu.result }}',
          '${{ needs.integration-docker.result }}'
          ])"
```

- [ ] **Step 3: Validate workflow YAML parses**

Run: `yamllint .github/workflows/tests.yml`

Expected: zero errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci(docker): add integration-docker job and wire into all_green"
```

---

## Phase 11 — Docs, deps, and argument_specs

### Task 14: Wire `community.docker` into galaxy.yml and requirements.txt

**Files:**
- Modify: `galaxy.yml`
- Modify: `requirements.txt`

- [ ] **Step 1: Read current `galaxy.yml`**

Run: `cat galaxy.yml`

- [ ] **Step 2: Update the description, tags, and dependencies**

Edit `galaxy.yml` so the relevant blocks read:
```yaml
description: >-
  Reusable Molecule provisioner playbooks and roles (podman, kubevirt, qemu, docker) for
  testing other Ansible collections without copy-pasting create/destroy/prepare
  automation per repo.
tags:
  - molecule
  - testing
  - podman
  - kubevirt
  - qemu
  - docker

dependencies:
  containers.podman: ">=1.10.0"
  kubernetes.core: ">=3.0.0"
  community.crypto: ">=2.0.0"
  community.docker: ">=4.0.0"
```

(Leave `version:` alone per the user's "don't worry about versioning" guidance.)

- [ ] **Step 3: Add an explicit comment about community.docker in requirements.txt**

Edit `requirements.txt` to read:
```
# TO-DO: add python packages that are required for this collection
# Note: community.docker (declared in galaxy.yml) requires the `docker` python
# package on the controller. Consumers running the docker backend must install:
#   pip install docker
```

- [ ] **Step 4: Confirm `ansible-galaxy collection list` still parses the file**

Run: `ansible-galaxy collection list 2>&1 | head -5`

Expected: no parse errors; `david_igou.molecule_provisioners` is listed.

- [ ] **Step 5: Commit**

```bash
git add galaxy.yml requirements.txt
git commit -m "feat(docker): declare community.docker dependency in galaxy.yml"
```

### Task 15: Write `roles/docker/meta/argument_specs.yml`

**Files:**
- Create: `roles/docker/meta/argument_specs.yml`

- [ ] **Step 1: Write the argument_specs**

Create `roles/docker/meta/argument_specs.yml`:
```yaml
---
argument_specs:
  main:
    short_description: >-
      Default entry point — does nothing on its own; use tasks_from=create|destroy|prepare.
    options: {}
  create:
    short_description: Create docker containers and networks for hosts in groups['molecule'].
    options:
      mp_docker_role_defaults:
        type: dict
        description: >-
          Per-host field defaults (command_handling, privileged, capabilities, etc.).
          Layered as: this dict <- mp_defaults.docker <- hostvars[item].mp.docker.
      mp_docker_async_timeout:
        type: int
        default: 7200
        description: Async timeout (seconds) for bulk container create.
      mp_docker_async_retries:
        type: int
        default: 300
        description: Number of times to poll for async completion.
      mp_docker_async_delay:
        type: int
        default: 24
        description: >-
          Seconds between async_status polls. retries * delay should
          equal async_timeout (default 300 * 24 = 7200).
      mp_docker_reserved_networks:
        type: list
        elements: str
        default: [bridge, host, none]
        description: Network names that are never created or deleted by the role.
  destroy:
    short_description: Destroy docker containers and (non-reserved) networks.
    options:
      mp_docker_role_defaults:
        type: dict
      mp_docker_async_timeout:
        type: int
        default: 7200
      mp_docker_async_retries:
        type: int
        default: 300
      mp_docker_async_delay:
        type: int
        default: 24
      mp_docker_reserved_networks:
        type: list
        elements: str
        default: [bridge, host, none]
  prepare:
    short_description: Run docker-side preparation tasks against created containers.
    options: {}
```

- [ ] **Step 2: Lint pass**

Run: `ansible-lint roles/docker/`

Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add roles/docker/meta/argument_specs.yml
git commit -m "docs(docker): declare argument_specs for ansible-doc"
```

### Task 16: Write `roles/docker/README.md`

**Files:**
- Create: `roles/docker/README.md`

- [ ] **Step 1: Write the README**

Create `roles/docker/README.md`:
```markdown
# `david_igou.molecule_provisioners.docker`

Molecule provisioner role for docker containers. Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers, which read `mp_backend` from the molecule group's hostvars.

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Computes per-host merged specs, creates user-defined docker networks, then creates containers from `hostvars[item].mp.docker.*` for each host in `groups['molecule']`. Writes `ansible_connection: community.docker.docker` per host into the runtime inventory file. |
| `destroy` | Removes those containers and any non-reserved networks. |
| `prepare` | Installs `sudo` inside each container. |

## Inputs (per-host, in inventory)

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            docker:
              image: docker.io/...:tag         # required
              command: /sbin/init              # optional
              command_handling: compatibility  # optional, role default 'compatibility'
              override_command: true           # optional, role default true
              hostname: <str>                  # optional, defaults to inventory_hostname
              privileged: false                # optional, role default false
              user: <str>                      # optional
              tty: <bool>                      # optional
              pid_mode: <str>                  # optional
              cgroupns_mode: <str>             # optional
              runtime: <str>                   # optional
              platform: <str>                  # optional
              capabilities: []                 # optional
              security_opts: [<str>]           # optional
              sysctls: {<k>: <v>}              # optional
              ulimits: [<str>]                 # optional
              devices: [<str>]                 # optional
              volumes: []                      # optional
              mounts: [<dict>]                 # optional
              tmpfs: [<str>]                   # optional
              shm_size: <str>                  # optional
              networks: [{name: <str>}]        # optional; role creates/deletes the network
              network_mode: <str>              # optional
              networks_cli_compatible: true    # optional, role default true
              purge_networks: <bool>           # optional
              dns_servers: [<str>]             # optional
              etc_hosts: {<host>: <ip>}        # optional
              exposed_ports: [<str>]           # optional
              published_ports: [<str>]         # optional
              links: [<str>]                   # optional
              env: {}                          # optional
              labels: {}                       # optional
              restart_policy: <str>            # optional
              restart_retries: <int>           # optional
              stop_signal: <str>               # optional
              kill_signal: <str>               # optional
              memory: <str>                    # optional
              memory_swap: <str>               # optional
              # destroy-time
              force_kill: true                 # optional, role default true
              keep_volumes: true               # optional, role default true
```

Shared defaults can be hoisted into `mp_defaults.docker` in `inventory/group_vars/molecule.yml` (overrides role defaults; per-host fields override mp_defaults). Field resolution order in the role: role defaults <- `mp_defaults.docker` <- `hostvars[item].mp.docker`.

## Role-level overrides

See `defaults/main.yml` (`mp_docker_role_defaults`, `mp_docker_async_*`, `mp_docker_reserved_networks`).

## Prerequisites

- A reachable local docker daemon.
- The `community.docker` collection (declared as a dependency in this collection's `galaxy.yml`).
- The `docker` python package on the controller (`pip install docker`).

## Out of scope

- Image build at create time (`Dockerfile.j2`, `pre_build_image`, `buildargs`, `cache_from`). Ship a fully-prepared image.
- Private registry login (`docker_login`). Bake credentials into the docker daemon config or pre-pull the image.
- Remote docker daemons / TLS (`docker_host`, `cacert_path`, `cert_path`, `key_path`, `tls_verify`). Local socket only.
```

- [ ] **Step 2: Commit**

```bash
git add roles/docker/README.md
git commit -m "docs(docker): role README with schema and prereqs"
```

### Task 17: Update `docs/examples/` to include docker

**Files:**
- Modify: `docs/examples/inventory/hosts.yml`
- Modify: `docs/examples/inventory/group_vars/molecule.yml`

- [ ] **Step 1: Add `mp.docker` block to hosts.yml**

Edit `docs/examples/inventory/hosts.yml`:
```yaml
---
# Example inventory shape for david_igou.molecule_provisioners.
#
# Each host has `mp.podman`, `mp.kubevirt`, and `mp.docker` blocks so the same
# inventory works under any backend. Switch backends at runtime by setting
# PROVISIONER=podman|kubevirt|docker — group_vars/molecule.yml maps that to
# mp_backend.

all:
  children:
    molecule:
      hosts:
        ubuntu-24:
          mp:
            podman:
              image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
            kubevirt:
              image: quay.io/containerdisks/ubuntu:24.04
              ssh_user: ubuntu
            docker:
              image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
```

- [ ] **Step 2: Add `mp_defaults.docker` to group_vars/molecule.yml**

Edit `docs/examples/inventory/group_vars/molecule.yml`:
```yaml
---
# Backend selector. Drives which role the dispatcher includes.
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

# Per-backend defaults shared across hosts. Per-host overrides go in hosts.yml
# under mp.<backend>.<field>. Role-shipped defaults apply when neither is set.
mp_defaults:
  podman:
    command: /sbin/init
    privileged: true
  kubevirt:
    namespace: molecule
    memory: 1Gi
    ssh_user: cloud-user
  docker:
    command: /sbin/init
    privileged: true
```

- [ ] **Step 3: Commit**

```bash
git add docs/examples/inventory/
git commit -m "docs(examples): add docker block to inventory + defaults example"
```

### Task 18: Update top-level `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the architecture paragraph**

Find the "Architecture (one-paragraph version)" heading and update the surrounding paragraph so the role list reads `roles/podman`, `roles/kubevirt`, `roles/qemu`, `roles/docker`. Update the phrase "one of two roles" to "one of four roles" (or remove the count entirely — simpler).

Specifically, edit the paragraph that currently reads "into one of two roles (`roles/podman`, `roles/kubevirt`)" to read "into one of the backend roles (`roles/podman`, `roles/kubevirt`, `roles/qemu`, `roles/docker`)".

- [ ] **Step 2: Update the "Key files" list**

Find the "Key files" section. Add a line for the docker role's task layout, mirroring the podman line, just after the qemu entry:
```
- `roles/docker/tasks/{create,destroy,prepare,_spec_merge,_validate,_networks}.yml` — docker lifecycle. `_networks.yml` is shared between create and destroy.
```

- [ ] **Step 3: Update the "Public contract" schema block**

In the "Public contract (the thing we don't break without a major bump)" section, append a `docker:` schema block under the inventory shape example, after the `kubevirt:` block:
```yaml
            docker:                      # required when mp_backend == docker
              image: <str>               # required
              # optional: command, command_handling, override_command, hostname,
              #   privileged, user, tty, pid_mode, cgroupns_mode, runtime, platform,
              #   capabilities, security_opts, sysctls, ulimits, devices,
              #   volumes, mounts, tmpfs, shm_size,
              #   networks, network_mode, networks_cli_compatible, purge_networks,
              #   dns_servers, etc_hosts, exposed_ports, published_ports, links,
              #   env, labels, restart_policy, restart_retries, stop_signal, kill_signal,
              #   memory, memory_swap, force_kill, keep_volumes
```

- [ ] **Step 4: Update the "Common commands" table**

Find the row for the kubevirt self-test and add a docker row right after it:
```
| Run docker self-test | `PROVISIONER=docker pytest tests/integration -v -k default` |
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): mention docker backend in architecture + schema"
```

---

## Phase 12 — Final verification

### Task 19: Full lint + test sweep

**Files:** none (verification only)

- [ ] **Step 1: ansible-lint clean across the whole repo**

Run: `ansible-lint`

Expected: zero errors. Investigate and fix any new warnings introduced by the docker role; do not suppress globally.

- [ ] **Step 2: yamllint clean**

Run: `yamllint .`

Expected: zero errors.

- [ ] **Step 3: Docker fast tests pass**

Run: `pytest tests/integration/docker/ -v`

Expected: all four tests pass (`test_valid_minimal_passes_validation`, `test_missing_image_fails_with_message`, `test_networks_collector_dedupes_and_skips_reserved`, `test_destroy_is_idempotent_on_fresh_state`).

- [ ] **Step 4: Full molecule scenario passes under PROVISIONER=docker**

Run: `PROVISIONER=docker ANSIBLE_COLLECTIONS_PATH="$HOME/.ansible/collections" pytest tests/integration -v -k default -s -o addopts=""`

Expected: exit 0 with `PLAY RECAP` showing successful create/prepare/converge/verify/destroy.

- [ ] **Step 5: Confirm no regression on existing backends**

Run:
```bash
PROVISIONER=podman ANSIBLE_COLLECTIONS_PATH="$HOME/.ansible/collections" \
  pytest tests/integration -v -k default
```

Expected: exit 0. (Skip kubevirt/qemu locally unless the devcontainer has the needed clusters/tools — CI will catch regressions there.)

- [ ] **Step 6: Final commit if anything was tweaked in steps 1–5, otherwise no commit needed**

```bash
git status
# If there are uncommitted fixes, group them logically:
# git add <files>; git commit -m "fix(docker): <what>"
```
