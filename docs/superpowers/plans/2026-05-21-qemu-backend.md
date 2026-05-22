> **⚠️ SUPERSEDED 2026-05-21.** The libvirt driver path was removed from v1.1 after local verification surfaced multiple interacting bugs. The plan below stays as a historical trail of the original two-driver design. The actual v1.1 implementation follows `docs/superpowers/plans/2026-05-21-qemu-simplify-process-only.md` (process-driver only, SLIRP-only).

---

# qemu Backend (v1.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `qemu` backend to `david_igou.molecule_provisioners` with per-host driver dispatch (`libvirt` or `process`), SLIRP and libvirt-NAT networking, and module-first implementation. Ships as v1.1.0.

**Architecture:** New `roles/qemu/` parallel to the existing podman/kubevirt roles. The top-level dispatcher (`playbooks/{create,destroy,prepare}.yml`) already accepts a third backend — only `playbooks/group_vars/all.yml`'s `mp_supported_backends` list needs an entry. Inside the role, per-host `mp.qemu.driver` selects `_create_libvirt.yml` vs `_create_process.yml`. Shared task files handle image cache, qcow2 overlay, NoCloud seed ISO, and runtime inventory write-back. Validation is fail-fast asserts up front (mirrors kubevirt's `ssh_service.type` pattern).

**Tech Stack:** Ansible 2.15+, `community.libvirt` >= 1.3.0 (new dep), `community.crypto` (already a dep), `containers.podman` and `kubernetes.core` (already deps, untouched). Host-tool prereqs: `qemu-system-x86_64`, `qemu-img`, `cloud-localds` (or `genisoimage`), `libvirtd` for the libvirt driver. Test fixtures use direct `ansible-playbook` invocations under `tests/integration/qemu/` for fast TDD; the molecule self-test scenario at `extensions/molecule/qemu/` is the end-to-end gate.

**Spec reference:** [`docs/superpowers/specs/2026-05-21-qemu-backend-design.md`](../specs/2026-05-21-qemu-backend-design.md)

---

## File Map

**Create:**
- `roles/qemu/defaults/main.yml`
- `roles/qemu/meta/main.yml`
- `roles/qemu/tasks/main.yml`
- `roles/qemu/tasks/create.yml`
- `roles/qemu/tasks/destroy.yml`
- `roles/qemu/tasks/prepare.yml`
- `roles/qemu/tasks/_spec_merge.yml`
- `roles/qemu/tasks/_image_cache.yml`
- `roles/qemu/tasks/_overlay.yml`
- `roles/qemu/tasks/_seed_iso.yml`
- `roles/qemu/tasks/_create_libvirt.yml`
- `roles/qemu/tasks/_create_process.yml`
- `roles/qemu/tasks/_destroy_libvirt.yml`
- `roles/qemu/tasks/_destroy_process.yml`
- `roles/qemu/tasks/_runtime_inventory.yml`
- `roles/qemu/templates/domain.xml.j2`
- `roles/qemu/templates/user-data.j2`
- `roles/qemu/templates/meta-data.j2`
- `extensions/molecule/qemu/molecule.yml`
- `extensions/molecule/qemu/create.yml`
- `extensions/molecule/qemu/destroy.yml`
- `extensions/molecule/qemu/prepare.yml`
- `extensions/molecule/qemu/converge.yml`
- `extensions/molecule/qemu/verify.yml`
- `extensions/molecule/qemu/inventory/hosts.yml`
- `extensions/molecule/qemu/inventory/group_vars/molecule.yml`
- `tests/integration/qemu/__init__.py`
- `tests/integration/qemu/test_qemu_unit.py`
- `tests/integration/qemu/fixtures/valid_minimal.yml`
- `tests/integration/qemu/fixtures/bad_driver.yml`
- `tests/integration/qemu/fixtures/process_nat_invalid.yml`
- `tests/integration/qemu/fixtures/missing_image.yml`
- `tests/integration/qemu/assertions/run_validate.yml`
- `tests/integration/qemu/assertions/run_image_cache.yml`
- `tests/integration/qemu/assertions/run_seed_iso.yml`
- `changelogs/fragments/qemu-backend.yml`

**Modify:**
- `playbooks/group_vars/all.yml` — add `qemu` to `mp_supported_backends`
- `galaxy.yml` — bump version to `1.1.0`, add `community.libvirt: ">=1.3.0"`
- `README.md` — add `qemu` row, inventory example, prereqs; remove from "Out of scope"
- `docs/MIGRATION.md` — add "Migrating from `molecule-plugins[libvirt]`" subsection; remove from "What this collection does NOT support"
- `CHANGELOG.rst` — add v1.1.0 section
- `.github/workflows/tests.yml` — add `integration-qemu` job; add to `all_green` needs

---

## Phase 1 — Foundation

### Task 1: Wire qemu into the dispatcher's allow-list

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
```

- [ ] **Step 2: Add `qemu` to the list**

Replace the file with:
```yaml
---
# Loaded by every dispatcher play in playbooks/.
mp_supported_backends:
  - podman
  - kubevirt
  - qemu
```

- [ ] **Step 3: Verify dispatcher now accepts `qemu`**

Create a temporary inventory file `/tmp/qemu-dispatch-check.yml`:
```yaml
all:
  children:
    molecule:
      hosts:
        h1:
          mp:
            qemu:
              image: file:///nonexistent.qcow2
          mp_backend: qemu
          mp_defaults: {}
```

Run: `ansible-playbook -i /tmp/qemu-dispatch-check.yml playbooks/create.yml 2>&1 | head -40`

Expected: dispatcher gets past the "Validate backend" assert (no `mp_backend must be one of …` message) and instead fails at "Run provisioner create" (because `roles/qemu/` doesn't exist yet). Confirms the allow-list change works.

- [ ] **Step 4: Commit**

```bash
git add playbooks/group_vars/all.yml
git commit -m "feat(dispatcher): accept qemu as a supported backend"
```

---

### Task 2: Add `community.libvirt` dependency and bump version

**Files:**
- Modify: `galaxy.yml`

- [ ] **Step 1: Bump `version` and add the libvirt dep**

Open `galaxy.yml`. Change `version: 1.0.0` to `version: 1.1.0`. Under `dependencies:`, add `community.libvirt: ">=1.3.0"` so the block becomes:

```yaml
dependencies:
  containers.podman: ">=1.10.0"
  kubernetes.core: ">=3.0.0"
  community.crypto: ">=2.0.0"
  community.libvirt: ">=1.3.0"
```

- [ ] **Step 2: Verify the file parses**

Run: `ansible-galaxy collection build --output-path /tmp/ . 2>&1 | tail -20`
Expected: Build succeeds, prints something like `Created collection for david_igou.molecule_provisioners at /tmp/david_igou-molecule_provisioners-1.1.0.tar.gz`.

Cleanup: `rm /tmp/david_igou-molecule_provisioners-1.1.0.tar.gz`

- [ ] **Step 3: Install the libvirt collection locally so subsequent task runs work**

Run: `ansible-galaxy collection install community.libvirt:'>=1.3.0' --upgrade`
Expected: `community.libvirt was installed successfully`.

- [ ] **Step 4: Commit**

```bash
git add galaxy.yml
git commit -m "feat(galaxy): bump to 1.1.0 and add community.libvirt dep"
```

---

### Task 3: Scaffold the qemu role skeleton

**Files:**
- Create: `roles/qemu/defaults/main.yml`
- Create: `roles/qemu/meta/main.yml`
- Create: `roles/qemu/tasks/main.yml`
- Create: `roles/qemu/tasks/create.yml`
- Create: `roles/qemu/tasks/destroy.yml`
- Create: `roles/qemu/tasks/prepare.yml`

- [ ] **Step 1: Create `roles/qemu/defaults/main.yml`**

```yaml
---
# Defaults for david_igou.molecule_provisioners.qemu.

# SSH keypair lives in the molecule ephemeral dir so destroy can find it.
mp_qemu_ssh_key_path: "{{ molecule_ephemeral_directory }}/identity_file"

# wait_for_connection timeout (seconds) for prepare phase.
# Boot under TCG is much slower than KVM — keep generous.
mp_qemu_wait_timeout: 180

# Base port for SLIRP host-forwards. ansible_port = base + host index.
mp_qemu_slirp_port_base: 2222

# Image cache root. Honours XDG_CACHE_HOME, falls back to ~/.cache/molecule-qemu.
mp_qemu_image_cache_dir: >-
  {{ (lookup('env', 'XDG_CACHE_HOME') | default(ansible_env.HOME ~ '/.cache', true))
     ~ '/molecule-qemu' }}

# Allowed enum values. Validation refuses anything else.
mp_qemu_allowed_drivers:
  - libvirt
  - process
mp_qemu_allowed_network_modes:
  - slirp
  - nat

# Per-host field defaults. Layered as: this dict <- mp_defaults.qemu <- hostvars[item].mp.qemu.
# Only `image` is required and is therefore absent from this dict.
mp_qemu_role_defaults:
  driver: libvirt
  uri: qemu:///session
  cpus: 2
  memory: 1024
  ssh_user: cloud-user
  network:
    mode: slirp
```

- [ ] **Step 2: Create `roles/qemu/meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: qemu
  author: David Igou
  description: Molecule provisioner role using qemu (libvirt or direct process driver)
  company: ""
  license: GPL-3.0-or-later
  min_ansible_version: "2.15"
  platforms:
    - name: GenericLinux
      versions: [all]
  galaxy_tags:
    - molecule
    - qemu
    - libvirt
    - testing
dependencies: []
```

- [ ] **Step 3: Create `roles/qemu/tasks/main.yml`**

```yaml
---
- name: "Qemu role: no default entry point"
  ansible.builtin.fail:
    msg: >-
      david_igou.molecule_provisioners.qemu has no main entry point.
      Use tasks_from=create|destroy|prepare via include_role.
```

- [ ] **Step 4: Create empty placeholders for the three entry points**

`roles/qemu/tasks/create.yml`:
```yaml
---
- name: "Qemu role: create entry point not yet implemented"
  ansible.builtin.fail:
    msg: "create.yml is a placeholder — implementation lands in subsequent tasks."
```

`roles/qemu/tasks/destroy.yml`:
```yaml
---
- name: "Qemu role: destroy entry point not yet implemented"
  ansible.builtin.fail:
    msg: "destroy.yml is a placeholder — implementation lands in subsequent tasks."
```

`roles/qemu/tasks/prepare.yml`:
```yaml
---
- name: Wait for the host to be reachable
  ansible.builtin.wait_for_connection:
    timeout: "{{ mp_qemu_wait_timeout }}"
    delay: 10
    sleep: 5
```

- [ ] **Step 5: Verify the dispatcher reaches the role now**

Run: `ansible-playbook -i /tmp/qemu-dispatch-check.yml playbooks/create.yml 2>&1 | tail -20`
Expected: Output ends with `create.yml is a placeholder — implementation lands in subsequent tasks.` (the role is found and dispatched).

- [ ] **Step 6: Commit**

```bash
git add roles/qemu/
git commit -m "feat(qemu): scaffold role with placeholders + defaults"
```

---

## Phase 2 — Validation (fast, no VMs)

### Task 4: Spec-merge include file

**Files:**
- Create: `roles/qemu/tasks/_spec_merge.yml`

- [ ] **Step 1: Create `_spec_merge.yml`**

```yaml
---
# Build _mp_specs[host] from three layers:
#   role defaults <- mp_defaults.qemu <- hostvars[item].mp.qemu
# Defensive on every layer so destroy can still merge for half-failed creates.
- name: Initialize qemu spec map
  ansible.builtin.set_fact:
    _mp_specs: {}

- name: Merge per-host specs with mp_defaults and role defaults
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: mp_qemu_role_defaults
                 | combine(mp_defaults['qemu'] | default({}), recursive=True)
                 | combine((hostvars[item].mp | default({}))['qemu'] | default({}), recursive=True)
         }, recursive=True) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

(`recursive=True` is required so the nested `network: { mode: ... }` block merges layer-by-layer rather than the outer layer wholesale-replacing the inner. Kubevirt's role uses shallow combine because its schema is flat; ours has `network.mode`.)

- [ ] **Step 2: Write a fixture inventory to unit-test the merge**

Create `tests/integration/qemu/fixtures/valid_minimal.yml`:
```yaml
all:
  children:
    molecule:
      hosts:
        h-minimal:
          mp:
            qemu:
              image: https://example.invalid/disk.qcow2
        h-overrides:
          mp:
            qemu:
              image: https://example.invalid/other.qcow2
              cpus: 4
              network:
                mode: nat
  vars:
    mp_backend: qemu
    mp_defaults:
      qemu:
        ssh_user: ubuntu
```

- [ ] **Step 3: Create the assertion playbook**

Create `tests/integration/qemu/assertions/run_validate.yml`:
```yaml
---
- name: Exercise spec-merge + validation
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Include role defaults
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../../roles/qemu/defaults/main.yml"
    - name: Surface mp_defaults from the molecule group
      ansible.builtin.set_fact:
        mp_defaults: "{{ hostvars[groups['molecule'][0]].mp_defaults | default({}) }}"
    - name: Merge specs
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/qemu/tasks/_spec_merge.yml"

    # Assertions for fixture valid_minimal.yml:
    - name: Assert h-minimal merged correctly
      ansible.builtin.assert:
        that:
          - _mp_specs['h-minimal'].image == 'https://example.invalid/disk.qcow2'
          - _mp_specs['h-minimal'].driver == 'libvirt'         # role default
          - _mp_specs['h-minimal'].cpus == 2                    # role default
          - _mp_specs['h-minimal'].ssh_user == 'ubuntu'         # from mp_defaults
          - _mp_specs['h-minimal'].network.mode == 'slirp'      # role default
    - name: Assert h-overrides merged correctly
      ansible.builtin.assert:
        that:
          - _mp_specs['h-overrides'].cpus == 4                  # host override wins
          - _mp_specs['h-overrides'].network.mode == 'nat'      # host override wins
          - _mp_specs['h-overrides'].ssh_user == 'ubuntu'       # mp_defaults still applies
          - _mp_specs['h-overrides'].driver == 'libvirt'        # role default still applies
```

- [ ] **Step 4: Run the assertion playbook (expect PASS)**

Run: `ansible-playbook -i tests/integration/qemu/fixtures/valid_minimal.yml tests/integration/qemu/assertions/run_validate.yml`
Expected: `ok=4 changed=2 failed=0` (or similar — last two asserts PASS).

- [ ] **Step 5: Commit**

```bash
git add roles/qemu/tasks/_spec_merge.yml tests/integration/qemu/
git commit -m "feat(qemu): per-host spec merge with recursive combine"
```

---

### Task 5: Fail-fast validation block

**Files:**
- Modify: `roles/qemu/tasks/create.yml`
- Create: `tests/integration/qemu/fixtures/bad_driver.yml`
- Create: `tests/integration/qemu/fixtures/process_nat_invalid.yml`
- Create: `tests/integration/qemu/fixtures/missing_image.yml`

- [ ] **Step 1: Replace `create.yml` with the spec-merge + validation block**

```yaml
---
# Phase 1: build spec map (shared between create and destroy).
- name: Merge per-host specs
  ansible.builtin.include_tasks: _spec_merge.yml

# Phase 2: validate. Fail fast before any side effects.
- name: Validate driver value per host
  ansible.builtin.assert:
    that: _mp_specs[item].driver in mp_qemu_allowed_drivers
    fail_msg: >-
      Host '{{ item }}' has unsupported qemu.driver
      '{{ _mp_specs[item].driver | default('(missing)') }}'.
      Allowed: {{ mp_qemu_allowed_drivers | join(', ') }}.
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Validate network.mode value per host
  ansible.builtin.assert:
    that: _mp_specs[item].network.mode in mp_qemu_allowed_network_modes
    fail_msg: >-
      Host '{{ item }}' has unsupported qemu.network.mode
      '{{ _mp_specs[item].network.mode | default('(missing)') }}'.
      Allowed: {{ mp_qemu_allowed_network_modes | join(', ') }}.
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Validate driver + network.mode compatibility
  ansible.builtin.assert:
    that: >-
      _mp_specs[item].driver == 'libvirt'
      or _mp_specs[item].network.mode == 'slirp'
    fail_msg: >-
      Host '{{ item }}' uses driver=process with network.mode=nat,
      which is not supported in v1.1. Either switch driver to libvirt
      or network.mode to slirp.
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Validate image is set per host
  ansible.builtin.assert:
    that:
      - _mp_specs[item].image is defined
      - (_mp_specs[item].image | string | length) > 0
    fail_msg: >-
      Host '{{ item }}' is missing qemu.image. Set
      hostvars.{{ item }}.mp.qemu.image to a qcow2 URL or local path.
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Ensure image cache dir exists and is writable
  ansible.builtin.file:
    path: "{{ mp_qemu_image_cache_dir }}"
    state: directory
    mode: "0755"

# Subsequent phases (image cache, seed ISO, driver dispatch, runtime inventory)
# land in later tasks. For now create.yml ends at validation.
```

- [ ] **Step 2: Re-run the assertion playbook with the valid fixture (should still PASS)**

Update `tests/integration/qemu/assertions/run_validate.yml` to include the validation block after the merge. Replace it with:

```yaml
---
- name: Exercise spec-merge + validation
  hosts: localhost
  connection: local
  gather_facts: true       # needed for ansible_env.HOME in image_cache_dir
  tasks:
    - name: Include role defaults
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../../roles/qemu/defaults/main.yml"
    - name: Surface mp_defaults from the molecule group
      ansible.builtin.set_fact:
        mp_defaults: "{{ hostvars[groups['molecule'][0]].mp_defaults | default({}) }}"
    - name: Run create entrypoint (which merges + validates + creates cache dir)
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/qemu/tasks/create.yml"
    - name: Assert image cache dir was created
      ansible.builtin.stat:
        path: "{{ mp_qemu_image_cache_dir }}"
      register: __cache_stat
    - name: Cache dir must be a directory
      ansible.builtin.assert:
        that: __cache_stat.stat.isdir
```

Run: `ansible-playbook -i tests/integration/qemu/fixtures/valid_minimal.yml tests/integration/qemu/assertions/run_validate.yml`
Expected: `failed=0`.

- [ ] **Step 3: Create the three negative-test fixtures**

`tests/integration/qemu/fixtures/bad_driver.yml`:
```yaml
all:
  children:
    molecule:
      hosts:
        h1:
          mp:
            qemu:
              image: https://example.invalid/disk.qcow2
              driver: vmware    # unsupported
  vars:
    mp_backend: qemu
    mp_defaults: {}
```

`tests/integration/qemu/fixtures/process_nat_invalid.yml`:
```yaml
all:
  children:
    molecule:
      hosts:
        h1:
          mp:
            qemu:
              image: https://example.invalid/disk.qcow2
              driver: process
              network:
                mode: nat
  vars:
    mp_backend: qemu
    mp_defaults: {}
```

`tests/integration/qemu/fixtures/missing_image.yml`:
```yaml
all:
  children:
    molecule:
      hosts:
        h1:
          mp:
            qemu:
              driver: libvirt
              # image deliberately absent
  vars:
    mp_backend: qemu
    mp_defaults: {}
```

- [ ] **Step 4: Run each negative fixture, confirm specific failure messages**

For each fixture, run:
```bash
ansible-playbook -i tests/integration/qemu/fixtures/bad_driver.yml tests/integration/qemu/assertions/run_validate.yml 2>&1 | tail -10
```
Expected: contains `unsupported qemu.driver 'vmware'`.

```bash
ansible-playbook -i tests/integration/qemu/fixtures/process_nat_invalid.yml tests/integration/qemu/assertions/run_validate.yml 2>&1 | tail -10
```
Expected: contains `driver=process with network.mode=nat, which is not supported`.

```bash
ansible-playbook -i tests/integration/qemu/fixtures/missing_image.yml tests/integration/qemu/assertions/run_validate.yml 2>&1 | tail -10
```
Expected: contains `is missing qemu.image`.

- [ ] **Step 5: Wire the assertion runs into pytest**

Create `tests/integration/qemu/__init__.py` (empty file).

Create `tests/integration/qemu/test_qemu_unit.py`:
```python
"""Fast, VM-less tests for the qemu role's validation and merge logic."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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


def test_bad_driver_fails_with_message() -> None:
    proc = _run("run_validate.yml", "bad_driver.yml")
    assert proc.returncode != 0
    assert "unsupported qemu.driver 'vmware'" in proc.stdout


def test_process_nat_combo_fails_with_message() -> None:
    proc = _run("run_validate.yml", "process_nat_invalid.yml")
    assert proc.returncode != 0
    assert "driver=process with network.mode=nat" in proc.stdout


def test_missing_image_fails_with_message() -> None:
    proc = _run("run_validate.yml", "missing_image.yml")
    assert proc.returncode != 0
    assert "is missing qemu.image" in proc.stdout
```

- [ ] **Step 6: Run pytest**

Run: `pytest tests/integration/qemu/test_qemu_unit.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add roles/qemu/tasks/create.yml tests/integration/qemu/
git commit -m "feat(qemu): fail-fast validation with negative-test coverage"
```

---

## Phase 3 — Image cache + seed ISO (shared concerns)

### Task 6: Image cache include file

**Files:**
- Create: `roles/qemu/tasks/_image_cache.yml`
- Modify: `roles/qemu/tasks/create.yml`
- Create: `tests/integration/qemu/assertions/run_image_cache.yml`
- Modify: `tests/integration/qemu/test_qemu_unit.py`

- [ ] **Step 1: Create `_image_cache.yml`**

```yaml
---
# For each unique image URL, ensure a cached base qcow2 exists.
# Cache layout: {{ mp_qemu_image_cache_dir }}/<sha256-of-url>/disk.qcow2
- name: Determine unique image specs
  ansible.builtin.set_fact:
    _mp_qemu_unique_images: >-
      {{ groups['molecule']
         | map('extract', hostvars)
         | map(attribute='mp.qemu')
         | list
         | items2dict(key_name='image', value_name='image_checksum') }}
  # Build a dict {url: checksum_or_none}. items2dict de-duplicates URLs;
  # the last checksum seen wins, which is fine since we expect the
  # same URL to carry the same checksum.

- name: Ensure per-image cache subdir exists
  ansible.builtin.file:
    path: "{{ mp_qemu_image_cache_dir }}/{{ item.key | ansible.builtin.hash('sha256') }}"
    state: directory
    mode: "0755"
  loop: "{{ _mp_qemu_unique_images | dict2items }}"
  loop_control:
    label: "{{ item.key }}"

- name: Download base qcow2 (idempotent, cached)
  ansible.builtin.get_url:
    url: "{{ item.key }}"
    dest: "{{ mp_qemu_image_cache_dir }}/{{ item.key | ansible.builtin.hash('sha256') }}/disk.qcow2"
    checksum: "{{ item.value | default(omit, true) }}"
    mode: "0644"
  loop: "{{ _mp_qemu_unique_images | dict2items }}"
  loop_control:
    label: "{{ item.key }}"

- name: Record cached base path per host in _mp_specs
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: _mp_specs[item] | combine({
             'base_image_path':
               mp_qemu_image_cache_dir ~ '/'
               ~ (_mp_specs[item].image | ansible.builtin.hash('sha256'))
               ~ '/disk.qcow2'
           })
         }, recursive=True) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

(`items2dict` collapses the duplicates; the `hash('sha256')` filter is `ansible.builtin.hash` per the spec.)

- [ ] **Step 2: Wire it into `create.yml` after validation**

Append to `roles/qemu/tasks/create.yml` (just before the trailing comment block):

```yaml
# Phase 3: download and cache base qcow2 images (idempotent).
- name: Cache base images
  ansible.builtin.include_tasks: _image_cache.yml
```

- [ ] **Step 3: Create an assertion playbook using a tiny local file as the "image"**

Create `tests/integration/qemu/assertions/run_image_cache.yml`:
```yaml
---
- name: Exercise image cache against a local file:// URL
  hosts: localhost
  connection: local
  gather_facts: true
  vars:
    _fake_image_src: /tmp/qemu-fake-image.qcow2
    _fake_image_url: "file:///tmp/qemu-fake-image.qcow2"
  tasks:
    - name: Create a tiny fake qcow2 (just any bytes)
      ansible.builtin.copy:
        content: "QFI\xfb fake qcow2 for cache test"
        dest: "{{ _fake_image_src }}"
        mode: "0644"

    # Rewrite the inventory's image URL to point at our local file:
    - name: Patch fixture host image to local file
      ansible.builtin.set_fact:
        hostvars: >-
          {{ hostvars | combine({
               groups['molecule'][0]:
                 hostvars[groups['molecule'][0]] | combine({
                   'mp': hostvars[groups['molecule'][0]].mp | combine({
                     'qemu': hostvars[groups['molecule'][0]].mp.qemu | combine({
                       'image': _fake_image_url
                     })
                   })
                 })
             }, recursive=True) }}

    - name: Include role defaults
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../../roles/qemu/defaults/main.yml"
    - name: Surface mp_defaults
      ansible.builtin.set_fact:
        mp_defaults: "{{ hostvars[groups['molecule'][0]].mp_defaults | default({}) }}"
    - name: Run create entrypoint
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/qemu/tasks/create.yml"

    - name: Compute expected cached path
      ansible.builtin.set_fact:
        _expected_path: >-
          {{ mp_qemu_image_cache_dir }}/{{ _fake_image_url | ansible.builtin.hash('sha256') }}/disk.qcow2
    - name: Stat the cached file
      ansible.builtin.stat:
        path: "{{ _expected_path }}"
      register: __cached
    - name: Assert cached image exists
      ansible.builtin.assert:
        that:
          - __cached.stat.exists
          - __cached.stat.size > 0
    - name: Assert _mp_specs records base_image_path
      ansible.builtin.assert:
        that:
          - _mp_specs[groups['molecule'][0]].base_image_path == _expected_path

    - name: Cleanup fake source
      ansible.builtin.file:
        path: "{{ _fake_image_src }}"
        state: absent
```

- [ ] **Step 4: Add the pytest case**

Append to `tests/integration/qemu/test_qemu_unit.py`:
```python
def test_image_cache_creates_cached_file() -> None:
    proc = _run("run_image_cache.yml", "valid_minimal.yml")
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 5: Run pytest**

Run: `pytest tests/integration/qemu/test_qemu_unit.py::test_image_cache_creates_cached_file -v`
Expected: passes.

- [ ] **Step 6: Commit**

```bash
git add roles/qemu/tasks/_image_cache.yml roles/qemu/tasks/create.yml tests/integration/qemu/
git commit -m "feat(qemu): per-URL image cache via get_url with sha256 keying"
```

---

### Task 7: Cloud-init seed ISO

**Files:**
- Create: `roles/qemu/templates/user-data.j2`
- Create: `roles/qemu/templates/meta-data.j2`
- Create: `roles/qemu/tasks/_seed_iso.yml`
- Modify: `roles/qemu/tasks/create.yml`
- Create: `tests/integration/qemu/assertions/run_seed_iso.yml`
- Modify: `tests/integration/qemu/test_qemu_unit.py`

- [ ] **Step 1: Create `user-data.j2`**

```jinja
#cloud-config
users:
  - name: {{ _mp_specs[_host].ssh_user }}
    ssh_authorized_keys:
      - {{ temporary_ssh_public_key }}
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
chpasswd:
  expire: false
{% if _mp_specs[_host].disk_size is defined and _mp_specs[_host].disk_size %}
growpart:
  mode: auto
  devices: ['/']
runcmd:
  - resize2fs $(findmnt / -no SOURCE) || xfs_growfs /
{% endif %}
```

- [ ] **Step 2: Create `meta-data.j2`**

```jinja
instance-id: {{ _host }}-{{ ansible_date_time.epoch }}
local-hostname: {{ _host }}
```

- [ ] **Step 3: Create `_seed_iso.yml`**

```yaml
---
# Generate SSH keypair once for the whole molecule run.
- name: Generate SSH key pair
  community.crypto.openssh_keypair:
    path: "{{ mp_qemu_ssh_key_path }}"
    type: ed25519
  register: __mp_qemu_ssh_keypair

- name: Surface SSH public key
  ansible.builtin.set_fact:
    temporary_ssh_public_key: "{{ __mp_qemu_ssh_keypair.public_key }}"

- name: Detect cloud-localds availability
  ansible.builtin.command: which cloud-localds
  register: __mp_qemu_cloudlocalds
  changed_when: false
  failed_when: false

- name: Build per-host seed ISO
  vars:
    _host: "{{ item }}"
    _cidata_dir: "{{ molecule_ephemeral_directory }}/{{ item }}-cidata"
    _seed_iso: "{{ molecule_ephemeral_directory }}/{{ item }}-seed.iso"
  block:
    - name: "Ensure cidata dir for {{ item }}"
      ansible.builtin.file:
        path: "{{ _cidata_dir }}"
        state: directory
        mode: "0755"
    - name: "Render user-data for {{ item }}"
      ansible.builtin.template:
        src: user-data.j2
        dest: "{{ _cidata_dir }}/user-data"
        mode: "0644"
    - name: "Render meta-data for {{ item }}"
      ansible.builtin.template:
        src: meta-data.j2
        dest: "{{ _cidata_dir }}/meta-data"
        mode: "0644"
    - name: "Build seed ISO via cloud-localds for {{ item }}"
      ansible.builtin.command:
        cmd: "cloud-localds {{ _seed_iso }} {{ _cidata_dir }}/user-data {{ _cidata_dir }}/meta-data"
        creates: "{{ _seed_iso }}"
      when: __mp_qemu_cloudlocalds.rc == 0
    - name: "Build seed ISO via genisoimage for {{ item }}"
      ansible.builtin.command:
        cmd: "genisoimage -output {{ _seed_iso }} -volid cidata -joliet -rock {{ _cidata_dir }}/user-data {{ _cidata_dir }}/meta-data"
        creates: "{{ _seed_iso }}"
      when: __mp_qemu_cloudlocalds.rc != 0
    - name: "Record seed_iso path for {{ item }}"
      ansible.builtin.set_fact:
        _mp_specs: >-
          {{ _mp_specs | combine({
               item: _mp_specs[item] | combine({'seed_iso_path': _seed_iso})
             }, recursive=True) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 4: Append to `create.yml`**

```yaml
# Phase 4: build per-host NoCloud seed ISOs.
- name: Build seed ISOs
  ansible.builtin.include_tasks: _seed_iso.yml
```

- [ ] **Step 5: Create assertion playbook**

`tests/integration/qemu/assertions/run_seed_iso.yml`:
```yaml
---
- name: Exercise seed ISO build
  hosts: localhost
  connection: local
  gather_facts: true
  vars:
    molecule_ephemeral_directory: "{{ lookup('env', 'MOLECULE_EPHEMERAL_DIRECTORY')
                                       | default('/tmp/molecule-fake-ephemeral', true) }}"
    _fake_image_src: /tmp/qemu-fake-image.qcow2
  tasks:
    - name: Ensure ephemeral dir
      ansible.builtin.file:
        path: "{{ molecule_ephemeral_directory }}"
        state: directory
        mode: "0755"
    - name: Create a tiny fake qcow2
      ansible.builtin.copy:
        content: "QFI\xfb fake qcow2"
        dest: "{{ _fake_image_src }}"
        mode: "0644"
    - name: Patch host image
      ansible.builtin.set_fact:
        hostvars: >-
          {{ hostvars | combine({
               groups['molecule'][0]:
                 hostvars[groups['molecule'][0]] | combine({
                   'mp': hostvars[groups['molecule'][0]].mp | combine({
                     'qemu': hostvars[groups['molecule'][0]].mp.qemu | combine({
                       'image': 'file://' ~ _fake_image_src
                     })
                   })
                 })
             }, recursive=True) }}
    - name: Include role defaults
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../../roles/qemu/defaults/main.yml"
    - name: Surface mp_defaults
      ansible.builtin.set_fact:
        mp_defaults: "{{ hostvars[groups['molecule'][0]].mp_defaults | default({}) }}"
    - name: Run create entrypoint
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/qemu/tasks/create.yml"

    - name: Stat the seed ISO for the first host
      ansible.builtin.stat:
        path: "{{ _mp_specs[groups['molecule'][0]].seed_iso_path }}"
      register: __iso
    - name: Assert seed ISO exists and is non-empty
      ansible.builtin.assert:
        that:
          - __iso.stat.exists
          - __iso.stat.size > 0
    - name: Stat user-data
      ansible.builtin.stat:
        path: "{{ molecule_ephemeral_directory }}/{{ groups['molecule'][0] }}-cidata/user-data"
      register: __ud
    - name: Assert user-data exists
      ansible.builtin.assert:
        that: __ud.stat.exists
    - name: Read user-data
      ansible.builtin.slurp:
        src: "{{ molecule_ephemeral_directory }}/{{ groups['molecule'][0] }}-cidata/user-data"
      register: __ud_content
    - name: Assert user-data has the expected user
      ansible.builtin.assert:
        that:
          - "'name: ubuntu' in (__ud_content.content | b64decode)"
          - "'ssh-ed25519' in (__ud_content.content | b64decode)"
```

(Asserts on `name: ubuntu` because `valid_minimal.yml` sets `ssh_user: ubuntu` via `mp_defaults`. The pub key starts with `ssh-ed25519` since the keypair module uses ed25519.)

- [ ] **Step 6: Add the pytest case**

Append to `tests/integration/qemu/test_qemu_unit.py`:
```python
def test_seed_iso_is_built_and_contains_user(tmp_path) -> None:
    import os
    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "valid_minimal.yml"),
         str(ASSERTIONS / "run_seed_iso.yml")],
        cwd=COLLECTION_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 7: Run pytest**

Run: `pytest tests/integration/qemu/test_qemu_unit.py::test_seed_iso_is_built_and_contains_user -v`
Expected: passes (requires `cloud-localds` or `genisoimage` on PATH — if missing, install `cloud-image-utils` package).

- [ ] **Step 8: Commit**

```bash
git add roles/qemu/tasks/_seed_iso.yml roles/qemu/templates/ roles/qemu/tasks/create.yml tests/integration/qemu/
git commit -m "feat(qemu): NoCloud seed ISO build with cloud-localds and genisoimage fallback"
```

---

## Phase 4 — Process driver path

### Task 8: Overlay creation for the process driver

**Files:**
- Create: `roles/qemu/tasks/_overlay.yml`
- Modify: `roles/qemu/tasks/create.yml`

- [ ] **Step 1: Create `_overlay.yml`** (process branch only; libvirt branch added in Task 12)

```yaml
---
# Per-host overlay creation.
# Process driver: shell out to qemu-img (no daemon, no virt_volume).
# Libvirt driver: deferred to Task 12.
- name: "Create qcow2 overlay (process driver) for {{ item }}"
  ansible.builtin.command:
    cmd: >-
      qemu-img create -f qcow2
      -F qcow2 -b {{ _mp_specs[item].base_image_path }}
      {{ molecule_ephemeral_directory }}/{{ item }}.qcow2
    creates: "{{ molecule_ephemeral_directory }}/{{ item }}.qcow2"
  when: _mp_specs[item].driver == 'process'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: "Resize overlay (process driver) for {{ item }}"
  ansible.builtin.command:
    cmd: "qemu-img resize {{ molecule_ephemeral_directory }}/{{ item }}.qcow2 {{ _mp_specs[item].disk_size }}"
  when:
    - _mp_specs[item].driver == 'process'
    - _mp_specs[item].disk_size is defined
    - _mp_specs[item].disk_size
  changed_when: true
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Record overlay path per host
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: _mp_specs[item] | combine({
             'overlay_path': molecule_ephemeral_directory ~ '/' ~ item ~ '.qcow2'
           })
         }, recursive=True) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 2: Append to `create.yml`**

```yaml
# Phase 5: per-host qcow2 overlay (process driver only — libvirt branch added later).
- name: Create per-VM overlays
  ansible.builtin.include_tasks: _overlay.yml
```

- [ ] **Step 3: Commit (no test yet — exercised by the process driver E2E test in Task 11)**

```bash
git add roles/qemu/tasks/_overlay.yml roles/qemu/tasks/create.yml
git commit -m "feat(qemu): qcow2 overlay creation for process driver"
```

---

### Task 9: Process-driver create

**Files:**
- Create: `roles/qemu/tasks/_create_process.yml`
- Modify: `roles/qemu/tasks/create.yml`

- [ ] **Step 1: Create `_create_process.yml`**

```yaml
---
# Per-host process-driver launch. KVM detection runs once at the top of create
# and stashes a fact; here we just consume it.
- name: "Launch qemu-system for {{ item }}"
  vars:
    _ssh_port: "{{ mp_qemu_slirp_port_base | int + (groups['molecule'].index(item) | int) }}"
    _accel: "{{ 'kvm:tcg' if _mp_qemu_kvm_ok | default(false) else 'tcg' }}"
    _cpu_flag: "{{ '-cpu host' if _mp_qemu_kvm_ok | default(false) else '' }}"
  ansible.builtin.command:
    argv:
      - qemu-system-x86_64
      - -machine
      - "accel={{ _accel }}"
      - -m
      - "{{ _mp_specs[item].memory | int }}"
      - -smp
      - "{{ _mp_specs[item].cpus | int }}"
      - -drive
      - "file={{ _mp_specs[item].overlay_path }},if=virtio,format=qcow2"
      - -cdrom
      - "{{ _mp_specs[item].seed_iso_path }}"
      - -netdev
      - "user,id=net0,hostfwd=tcp::{{ _ssh_port }}-:22"
      - -device
      - virtio-net-pci,netdev=net0
      - -daemonize
      - -pidfile
      - "{{ molecule_ephemeral_directory }}/{{ item }}.pid"
      - -qmp
      - "unix:{{ molecule_ephemeral_directory }}/{{ item }}.qmp,server,nowait"
      - -display
      - "none"
    creates: "{{ molecule_ephemeral_directory }}/{{ item }}.pid"
  when: _mp_specs[item].driver == 'process'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Record slirp ssh_port per host (process driver)
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: _mp_specs[item] | combine({
             'ssh_host': '127.0.0.1',
             'ssh_port': (mp_qemu_slirp_port_base | int + (groups['molecule'].index(item) | int))
           })
         }, recursive=True) }}
  when: _mp_specs[item].driver == 'process'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

(`-cpu host` is intentionally omitted from `argv` because it isn't needed under TCG and including it conditionally inside a static argv list is awkward. The role can be extended in a follow-up if KVM users want `-cpu host`. The spec calls this out — not a regression.)

- [ ] **Step 2: Append the KVM-detection fact and the process-create dispatch to `create.yml`**

Add to `create.yml` after the overlay phase:

```yaml
# Phase 6: detect KVM availability once (used by both libvirt and process drivers).
- name: Stat /dev/kvm
  ansible.builtin.stat:
    path: /dev/kvm
  register: __mp_qemu_kvm_stat
- name: Decide whether KVM is usable
  ansible.builtin.set_fact:
    _mp_qemu_kvm_ok: >-
      {{ __mp_qemu_kvm_stat.stat.exists
         and __mp_qemu_kvm_stat.stat.readable
         and __mp_qemu_kvm_stat.stat.writeable }}

# Phase 7: driver-specific create (process branch — libvirt branch added later).
- name: Launch process-driver VMs
  ansible.builtin.include_tasks: _create_process.yml
```

- [ ] **Step 3: Commit (E2E test follows in Task 11)**

```bash
git add roles/qemu/tasks/_create_process.yml roles/qemu/tasks/create.yml
git commit -m "feat(qemu): process-driver launch via qemu-system-x86_64 -daemonize"
```

---

### Task 10: Process-driver destroy

**Files:**
- Create: `roles/qemu/tasks/_destroy_process.yml`
- Modify: `roles/qemu/tasks/destroy.yml`

- [ ] **Step 1: Create `_destroy_process.yml`**

```yaml
---
- name: "Slurp pidfile for {{ item }}"
  ansible.builtin.slurp:
    src: "{{ molecule_ephemeral_directory }}/{{ item }}.pid"
  register: __mp_qemu_pidfile
  failed_when: false

- name: "Compute pid for {{ item }}"
  ansible.builtin.set_fact:
    _mp_qemu_pid: >-
      {{ (__mp_qemu_pidfile.content | default('') | b64decode | trim)
         if (__mp_qemu_pidfile is mapping
             and ('content' in __mp_qemu_pidfile))
         else '' }}

- name: "Stat /proc/{{ _mp_qemu_pid }}/exe for {{ item }}"
  ansible.builtin.stat:
    path: "/proc/{{ _mp_qemu_pid }}/exe"
    follow: true
  register: __mp_qemu_proc_exe
  when: _mp_qemu_pid | length > 0
  failed_when: false

- name: "Send SIGTERM to {{ item }} (pid={{ _mp_qemu_pid }})"
  ansible.builtin.command:
    cmd: "kill -TERM {{ _mp_qemu_pid }}"
  when:
    - _mp_qemu_pid | length > 0
    - __mp_qemu_proc_exe.stat.exists | default(false)
    - "'qemu-system' in (__mp_qemu_proc_exe.stat.lnk_source | default(''))"
  changed_when: true
  failed_when: false

- name: "Wait for pidfile removal for {{ item }}"
  ansible.builtin.wait_for:
    path: "{{ molecule_ephemeral_directory }}/{{ item }}.pid"
    state: absent
    timeout: 30
  when: _mp_qemu_pid | length > 0
  failed_when: false

- name: "Force-kill {{ item }} if still alive"
  ansible.builtin.command:
    cmd: "kill -KILL {{ _mp_qemu_pid }}"
  when:
    - _mp_qemu_pid | length > 0
    - __mp_qemu_proc_exe.stat.exists | default(false)
  changed_when: true
  failed_when: false

- name: "Remove process-driver artifacts for {{ item }}"
  ansible.builtin.file:
    path: "{{ molecule_ephemeral_directory }}/{{ item }}{{ ext }}"
    state: absent
  loop:
    - ".qcow2"
    - ".pid"
    - ".qmp"
    - "-seed.iso"
  loop_control:
    loop_var: ext
```

- [ ] **Step 2: Replace `destroy.yml` with the dispatch**

```yaml
---
- name: Merge per-host specs (defensive)
  ansible.builtin.include_tasks: _spec_merge.yml

- name: Destroy process-driver VMs
  ansible.builtin.include_tasks: _destroy_process.yml
  when: _mp_specs[item].driver | default('libvirt') == 'process'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

# Libvirt destroy dispatch is appended in Task 14.

- name: Remove cidata directories
  ansible.builtin.file:
    path: "{{ molecule_ephemeral_directory }}/{{ item }}-cidata"
    state: absent
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

(Loop-over-include syntax: `include_tasks` inside a loop runs the file once per loop item with `item` set. The destroy file references `{{ item }}` throughout.)

- [ ] **Step 3: Commit**

```bash
git add roles/qemu/tasks/_destroy_process.yml roles/qemu/tasks/destroy.yml
git commit -m "feat(qemu): process-driver destroy with pid-confirmation and artifact cleanup"
```

---

### Task 11: Runtime inventory + process-driver end-to-end test

**Files:**
- Create: `roles/qemu/tasks/_runtime_inventory.yml`
- Modify: `roles/qemu/tasks/create.yml`
- Create: `tests/integration/qemu/assertions/run_process_e2e.yml`
- Modify: `tests/integration/qemu/test_qemu_unit.py`

- [ ] **Step 1: Create `_runtime_inventory.yml`**

```yaml
---
- name: Initialize runtime hosts dict
  ansible.builtin.set_fact:
    __mp_qemu_runtime_hosts: {}

- name: Build per-host runtime entries
  ansible.builtin.set_fact:
    __mp_qemu_runtime_hosts: >-
      {{ __mp_qemu_runtime_hosts | combine({
           item: {
             'ansible_host': _mp_specs[item].ssh_host,
             'ansible_port': _mp_specs[item].ssh_port,
             'ansible_user': _mp_specs[item].ssh_user,
             'ansible_ssh_private_key_file': mp_qemu_ssh_key_path,
             'ansible_connection': 'ssh',
             'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
           }
         }) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Ensure ephemeral inventory dir exists
  ansible.builtin.file:
    path: "{{ molecule_ephemeral_directory }}/inventory"
    state: directory
    mode: "0755"

- name: Write runtime inventory file
  vars:
    runtime_inventory:
      all:
        hosts: "{{ __mp_qemu_runtime_hosts }}"
  ansible.builtin.copy:
    content: "{{ runtime_inventory | to_nice_yaml }}"
    dest: "{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml"
    mode: "0600"

- name: Refresh inventory
  ansible.builtin.meta: refresh_inventory
```

- [ ] **Step 2: Append the runtime-inventory phase to `create.yml`**

```yaml
# Phase 8: write runtime connection inventory for the molecule prepare phase.
- name: Write runtime inventory
  ansible.builtin.include_tasks: _runtime_inventory.yml
```

- [ ] **Step 3: Create the process-driver E2E assertion playbook**

`tests/integration/qemu/assertions/run_process_e2e.yml`:
```yaml
---
- name: Process-driver E2E (boot a tiny VM under TCG, SSH in, destroy)
  hosts: localhost
  connection: local
  gather_facts: true
  vars:
    molecule_ephemeral_directory: "{{ lookup('env', 'MOLECULE_EPHEMERAL_DIRECTORY')
                                       | mandatory }}"
  tasks:
    - name: Include role defaults
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../../roles/qemu/defaults/main.yml"
    - name: Surface mp_defaults from the molecule group
      ansible.builtin.set_fact:
        mp_defaults: "{{ hostvars[groups['molecule'][0]].mp_defaults | default({}) }}"
    - name: Run create
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/qemu/tasks/create.yml"

    - name: Assert pidfile exists for the process host
      ansible.builtin.stat:
        path: "{{ molecule_ephemeral_directory }}/{{ groups['molecule'][0] }}.pid"
      register: __pid
    - name: pidfile must exist
      ansible.builtin.assert:
        that: __pid.stat.exists

    - name: Assert runtime inventory file exists
      ansible.builtin.stat:
        path: "{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml"
      register: __inv
    - name: Runtime inventory must exist
      ansible.builtin.assert:
        that: __inv.stat.exists

    - name: Run destroy
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/qemu/tasks/destroy.yml"

    - name: pidfile must be gone after destroy
      ansible.builtin.stat:
        path: "{{ molecule_ephemeral_directory }}/{{ groups['molecule'][0] }}.pid"
      register: __pid_after
    - name: assert cleanup
      ansible.builtin.assert:
        that: not __pid_after.stat.exists
```

Use a fixture inventory `tests/integration/qemu/fixtures/process_slirp.yml` that points at a real cloud image URL with a pinned checksum. **Pin a real Ubuntu noble URL + sha256** when first running this — fetch the checksum from `https://cloud-images.ubuntu.com/noble/current/SHA256SUMS` and record it in the fixture.

`tests/integration/qemu/fixtures/process_slirp.yml`:
```yaml
all:
  children:
    molecule:
      hosts:
        ubuntu-process-slirp:
          mp:
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              # NOTE: pin this to the current checksum from SHA256SUMS at implementation time.
              image_checksum: "sha256:REPLACE_AT_IMPL_TIME"
              driver: process
              memory: 1024
              cpus: 2
              ssh_user: ubuntu
              network:
                mode: slirp
  vars:
    mp_backend: qemu
    mp_defaults:
      qemu:
        ssh_user: ubuntu
```

- [ ] **Step 4: Add the pytest case**

Append to `tests/integration/qemu/test_qemu_unit.py`:
```python
@pytest.mark.slow
def test_process_driver_e2e(tmp_path) -> None:
    import os
    import shutil
    if not shutil.which("qemu-system-x86_64"):
        pytest.skip("qemu-system-x86_64 not installed")
    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "process_slirp.yml"),
         str(ASSERTIONS / "run_process_e2e.yml")],
        cwd=COLLECTION_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 5: Run the E2E test**

Run: `pytest tests/integration/qemu/test_qemu_unit.py::test_process_driver_e2e -v -s`
Expected: passes (downloads the Ubuntu image once, ~600MB; boots under TCG; destroys). Will be slow (~3–5 min). If `qemu-system-x86_64` is missing, the test skips — install `qemu-system-x86 qemu-utils cloud-image-utils` first.

- [ ] **Step 6: Commit**

```bash
git add roles/qemu/tasks/_runtime_inventory.yml roles/qemu/tasks/create.yml tests/integration/qemu/
git commit -m "feat(qemu): runtime inventory write-back + process-driver E2E test"
```

---

## Phase 5 — Libvirt driver path

### Task 12: Libvirt overlay (transient pool + virt_volume)

**Files:**
- Modify: `roles/qemu/tasks/_overlay.yml`

- [ ] **Step 1: Add the libvirt branch to `_overlay.yml`**

Append to the existing `_overlay.yml` (before the "Record overlay path" task — that one stays at the bottom for both drivers):

Replace the file in full with:
```yaml
---
# Process-driver branch: qemu-img create.
- name: "Create qcow2 overlay (process driver) for {{ item }}"
  ansible.builtin.command:
    cmd: >-
      qemu-img create -f qcow2
      -F qcow2 -b {{ _mp_specs[item].base_image_path }}
      {{ molecule_ephemeral_directory }}/{{ item }}.qcow2
    creates: "{{ molecule_ephemeral_directory }}/{{ item }}.qcow2"
  when: _mp_specs[item].driver == 'process'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: "Resize overlay (process driver) for {{ item }}"
  ansible.builtin.command:
    cmd: "qemu-img resize {{ molecule_ephemeral_directory }}/{{ item }}.qcow2 {{ _mp_specs[item].disk_size }}"
  when:
    - _mp_specs[item].driver == 'process'
    - _mp_specs[item].disk_size is defined
    - _mp_specs[item].disk_size
  changed_when: true
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

# Libvirt-driver branch: transient pool + virt_volume.
- name: "Define transient storage pool (libvirt driver)"
  community.libvirt.virt_pool:
    command: define
    name: "molecule-{{ molecule_ephemeral_directory | basename }}"
    uri: "{{ _mp_specs[groups['molecule'][0]].uri }}"
    xml: |
      <pool type='dir'>
        <name>molecule-{{ molecule_ephemeral_directory | basename }}</name>
        <target>
          <path>{{ molecule_ephemeral_directory }}</path>
        </target>
      </pool>
  when: groups['molecule']
       | map('extract', _mp_specs)
       | selectattr('driver', '==', 'libvirt')
       | list | length > 0
  register: __mp_qemu_pool_define

- name: "Build transient storage pool (libvirt driver)"
  community.libvirt.virt_pool:
    command: build
    name: "molecule-{{ molecule_ephemeral_directory | basename }}"
    uri: "{{ _mp_specs[groups['molecule'][0]].uri }}"
  when: __mp_qemu_pool_define is changed
  failed_when: false

- name: "Activate transient storage pool (libvirt driver)"
  community.libvirt.virt_pool:
    state: active
    name: "molecule-{{ molecule_ephemeral_directory | basename }}"
    uri: "{{ _mp_specs[groups['molecule'][0]].uri }}"
  when: groups['molecule']
       | map('extract', _mp_specs)
       | selectattr('driver', '==', 'libvirt')
       | list | length > 0

- name: "Create qcow2 volume (libvirt driver) for {{ item }}"
  community.libvirt.virt_volume:
    command: create
    pool: "molecule-{{ molecule_ephemeral_directory | basename }}"
    name: "{{ item }}.qcow2"
    uri: "{{ _mp_specs[item].uri }}"
    xml: |
      <volume type='file'>
        <name>{{ item }}.qcow2</name>
        <capacity unit='bytes'>{{ _mp_specs[item].base_image_capacity | default(2147483648) }}</capacity>
        <target>
          <format type='qcow2'/>
        </target>
        <backingStore>
          <path>{{ _mp_specs[item].base_image_path }}</path>
          <format type='qcow2'/>
        </backingStore>
      </volume>
  when: _mp_specs[item].driver == 'libvirt'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: "Resize overlay (libvirt driver) for {{ item }}"
  ansible.builtin.command:
    cmd: "qemu-img resize {{ molecule_ephemeral_directory }}/{{ item }}.qcow2 {{ _mp_specs[item].disk_size }}"
  when:
    - _mp_specs[item].driver == 'libvirt'
    - _mp_specs[item].disk_size is defined
    - _mp_specs[item].disk_size
  changed_when: true
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Record overlay path per host
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: _mp_specs[item] | combine({
             'overlay_path': molecule_ephemeral_directory ~ '/' ~ item ~ '.qcow2',
             'pool_name': 'molecule-' ~ (molecule_ephemeral_directory | basename)
           })
         }, recursive=True) }}
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

(The capacity guess of 2 GiB is a placeholder — virt_volume's `xml:` mode requires an explicit capacity tag; libvirt extends as backing image is read. Future improvement: parse the base image's actual capacity via `qemu-img info --output=json` and slurp.)

- [ ] **Step 2: Commit**

```bash
git add roles/qemu/tasks/_overlay.yml
git commit -m "feat(qemu): libvirt-driver overlay via transient pool + virt_volume"
```

---

### Task 13: Libvirt domain XML template + create

**Files:**
- Create: `roles/qemu/templates/domain.xml.j2`
- Create: `roles/qemu/tasks/_create_libvirt.yml`
- Modify: `roles/qemu/tasks/create.yml`

- [ ] **Step 1: Create `domain.xml.j2`**

```jinja
<domain type='{{ "kvm" if _mp_qemu_kvm_ok else "qemu" }}'>
  <name>{{ _host }}</name>
  <memory unit='MiB'>{{ _mp_specs[_host].memory | int }}</memory>
  <vcpu placement='static'>{{ _mp_specs[_host].cpus | int }}</vcpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='{{ "host-passthrough" if _mp_qemu_kvm_ok else "qemu64" }}'/>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{{ _mp_specs[_host].overlay_path }}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{{ _mp_specs[_host].seed_iso_path }}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
{% if _mp_specs[_host].network.mode == 'slirp' %}
    <interface type='user'>
      <mac address='{{ _mp_specs[_host].mac }}'/>
      <model type='virtio'/>
      <portForward proto='tcp'>
        <range start='{{ _mp_specs[_host].ssh_port }}' to='22'/>
      </portForward>
    </interface>
{% else %}
    <interface type='network'>
      <mac address='{{ _mp_specs[_host].mac }}'/>
      <source network='default'/>
      <model type='virtio'/>
    </interface>
{% endif %}
    <serial type='pty'><target port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
  </devices>
</domain>
```

- [ ] **Step 2: Create `_create_libvirt.yml`**

```yaml
---
# Generate deterministic MAC per VM. `52:54:00` is libvirt/qemu's OUI prefix.
- name: Compute deterministic MAC per host
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: _mp_specs[item] | combine({
             'mac':
               '52:54:00:'
               ~ (((molecule_ephemeral_directory ~ '-' ~ item)
                   | ansible.builtin.hash('sha256'))[0:2]) ~ ':'
               ~ (((molecule_ephemeral_directory ~ '-' ~ item)
                   | ansible.builtin.hash('sha256'))[2:4]) ~ ':'
               ~ (((molecule_ephemeral_directory ~ '-' ~ item)
                   | ansible.builtin.hash('sha256'))[4:6])
           })
         }, recursive=True) }}
  when: _mp_specs[item].driver == 'libvirt'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

# For slirp hosts, set ssh_host/ssh_port (mirrors process driver).
- name: Set ssh connection facts (libvirt + slirp)
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: _mp_specs[item] | combine({
             'ssh_host': '127.0.0.1',
             'ssh_port': (mp_qemu_slirp_port_base | int + (groups['molecule'].index(item) | int))
           })
         }, recursive=True) }}
  when:
    - _mp_specs[item].driver == 'libvirt'
    - _mp_specs[item].network.mode == 'slirp'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

# Render and define the domain, then start it.
- name: "Render domain XML for {{ item }}"
  vars:
    _host: "{{ item }}"
  ansible.builtin.template:
    src: domain.xml.j2
    dest: "{{ molecule_ephemeral_directory }}/{{ item }}.domain.xml"
    mode: "0644"
  when: _mp_specs[item].driver == 'libvirt'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: "Define domain for {{ item }}"
  community.libvirt.virt:
    command: define
    xml: "{{ lookup('file', molecule_ephemeral_directory ~ '/' ~ item ~ '.domain.xml') }}"
    uri: "{{ _mp_specs[item].uri }}"
  when: _mp_specs[item].driver == 'libvirt'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: "Start domain for {{ item }}"
  community.libvirt.virt:
    name: "{{ item }}"
    state: running
    uri: "{{ _mp_specs[item].uri }}"
  when: _mp_specs[item].driver == 'libvirt'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 3: Append the libvirt create dispatch to `create.yml`**

Insert just before the "Phase 8: Write runtime inventory" block:

```yaml
# Phase 7b: launch libvirt-driver VMs (NAT pre-reservation handled inside).
- name: Launch libvirt-driver VMs
  ansible.builtin.include_tasks: _create_libvirt.yml
```

- [ ] **Step 4: Commit**

```bash
git add roles/qemu/templates/domain.xml.j2 roles/qemu/tasks/_create_libvirt.yml roles/qemu/tasks/create.yml
git commit -m "feat(qemu): libvirt-driver domain define + start (slirp networking)"
```

---

### Task 14: Libvirt destroy

**Files:**
- Create: `roles/qemu/tasks/_destroy_libvirt.yml`
- Modify: `roles/qemu/tasks/destroy.yml`

- [ ] **Step 1: Create `_destroy_libvirt.yml`**

```yaml
---
- name: "Stop domain for {{ item }}"
  community.libvirt.virt:
    name: "{{ item }}"
    state: destroyed
    uri: "{{ _mp_specs[item].uri | default('qemu:///session') }}"
  failed_when: false

- name: "Undefine domain for {{ item }}"
  community.libvirt.virt:
    name: "{{ item }}"
    command: undefine
    uri: "{{ _mp_specs[item].uri | default('qemu:///session') }}"
  failed_when: false

- name: "Remove volume for {{ item }}"
  community.libvirt.virt_volume:
    command: delete
    pool: "molecule-{{ molecule_ephemeral_directory | basename }}"
    name: "{{ item }}.qcow2"
    uri: "{{ _mp_specs[item].uri | default('qemu:///session') }}"
  failed_when: false

- name: "Remove libvirt-driver artifacts for {{ item }}"
  ansible.builtin.file:
    path: "{{ molecule_ephemeral_directory }}/{{ item }}{{ ext }}"
    state: absent
  loop:
    - ".qcow2"
    - ".domain.xml"
    - "-seed.iso"
  loop_control:
    loop_var: ext
```

- [ ] **Step 2: Append the libvirt destroy dispatch to `destroy.yml`**

Replace `destroy.yml` with:
```yaml
---
- name: Merge per-host specs (defensive)
  ansible.builtin.include_tasks: _spec_merge.yml

- name: Destroy process-driver VMs
  ansible.builtin.include_tasks: _destroy_process.yml
  when: _mp_specs[item].driver | default('libvirt') == 'process'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Destroy libvirt-driver VMs
  ansible.builtin.include_tasks: _destroy_libvirt.yml
  when: _mp_specs[item].driver | default('libvirt') == 'libvirt'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

# NAT-mode static reservations: remove from libvirt default network if any host
# was registered.
- name: Remove NAT static reservations (libvirt + nat hosts)
  community.libvirt.virt_net:
    command: modify
    name: default
    xml: "<host mac='{{ _mp_specs[item].mac }}'/>"
    uri: "{{ _mp_specs[item].uri | default('qemu:///session') }}"
    # community.libvirt.virt_net 'modify' with a <host mac=.../> XML removes the
    # entry matched by MAC.
  when:
    - _mp_specs[item].driver | default('libvirt') == 'libvirt'
    - _mp_specs[item].network.mode | default('slirp') == 'nat'
    - _mp_specs[item].mac is defined
  failed_when: false
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

# Tear down the transient pool — only if no other molecule scenario is using it
# (we name the pool after the ephemeral dir, so it's scoped per-scenario).
- name: Stop transient storage pool
  community.libvirt.virt_pool:
    state: inactive
    name: "molecule-{{ molecule_ephemeral_directory | basename }}"
    uri: "{{ _mp_specs[groups['molecule'][0]].uri | default('qemu:///session') }}"
  failed_when: false

- name: Undefine transient storage pool
  community.libvirt.virt_pool:
    command: undefine
    name: "molecule-{{ molecule_ephemeral_directory | basename }}"
    uri: "{{ _mp_specs[groups['molecule'][0]].uri | default('qemu:///session') }}"
  failed_when: false

- name: Remove cidata directories
  ansible.builtin.file:
    path: "{{ molecule_ephemeral_directory }}/{{ item }}-cidata"
    state: absent
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 3: Commit**

```bash
git add roles/qemu/tasks/_destroy_libvirt.yml roles/qemu/tasks/destroy.yml
git commit -m "feat(qemu): libvirt-driver destroy with pool/volume/NAT-reservation cleanup"
```

---

### Task 15: NAT pre-reservation (libvirt + nat hosts)

**Files:**
- Modify: `roles/qemu/tasks/_create_libvirt.yml`

- [ ] **Step 1: Insert the NAT reservation block at the top of `_create_libvirt.yml`** (after the MAC computation, before slirp ssh-facts setting)

After the "Compute deterministic MAC per host" task and before "Set ssh connection facts (libvirt + slirp)", insert:

```yaml
# NAT mode: read the default network's IPv4 config, compute a static IP outside
# the DHCP range, inject a <host> reservation per VM.
- name: Read default network XML (NAT hosts)
  community.libvirt.virt_net:
    command: get_xml
    name: default
    uri: "{{ _mp_specs[item].uri }}"
  register: __mp_qemu_default_net
  when:
    - _mp_specs[item].driver == 'libvirt'
    - _mp_specs[item].network.mode == 'nat'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
  run_once: true

- name: Parse default subnet base (e.g. '192.168.122.') from network XML
  ansible.builtin.set_fact:
    _mp_qemu_nat_subnet_base: >-
      {{ (__mp_qemu_default_net.results
          | selectattr('skipped', 'undefined')
          | list
          | first).get_xml
         | regex_search("<ip address='([0-9.]+)' netmask=", '\\1')
         | first
         | regex_replace('\\.[0-9]+$', '.') }}
  when:
    - __mp_qemu_default_net is defined
    - groups['molecule']
       | map('extract', _mp_specs)
       | selectattr('network.mode', 'equalto', 'nat')
       | list | length > 0

- name: Compute static IP per NAT host (high end of subnet, offset by index)
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: _mp_specs[item] | combine({
             'ssh_host': _mp_qemu_nat_subnet_base
                         ~ (200 + (groups['molecule'].index(item) | int)) | string,
             'ssh_port': 22
           })
         }, recursive=True) }}
  when:
    - _mp_specs[item].driver == 'libvirt'
    - _mp_specs[item].network.mode == 'nat'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Inject static <host> reservations into default network
  community.libvirt.virt_net:
    command: modify
    name: default
    xml: >-
      <host mac='{{ _mp_specs[item].mac }}'
            name='{{ item }}'
            ip='{{ _mp_specs[item].ssh_host }}'/>
    uri: "{{ _mp_specs[item].uri }}"
  when:
    - _mp_specs[item].driver == 'libvirt'
    - _mp_specs[item].network.mode == 'nat'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

(The IP range `200 + index` lands at `<subnet>.200`, `.201`, … — outside the typical `2..254` DHCP range. A future polish task can parse the actual range from XML; the spec calls this acceptable for v1.1.)

- [ ] **Step 2: Commit**

```bash
git add roles/qemu/tasks/_create_libvirt.yml
git commit -m "feat(qemu): NAT mode static IP reservation via virt_net modify"
```

---

## Phase 6 — Self-test scenario + CI

### Task 16: Molecule self-test scenario

**Files:**
- Create: `extensions/molecule/qemu/molecule.yml`
- Create: `extensions/molecule/qemu/create.yml`
- Create: `extensions/molecule/qemu/destroy.yml`
- Create: `extensions/molecule/qemu/prepare.yml`
- Create: `extensions/molecule/qemu/converge.yml`
- Create: `extensions/molecule/qemu/verify.yml`
- Create: `extensions/molecule/qemu/inventory/hosts.yml`
- Create: `extensions/molecule/qemu/inventory/group_vars/molecule.yml`

- [ ] **Step 1: Copy `molecule.yml` boilerplate**

`extensions/molecule/qemu/molecule.yml`:
```yaml
---
ansible:
  executor:
    args:
      ansible_playbook:
        - --inventory=inventory/
        - --inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/
  playbooks:
    create: create.yml
    destroy: destroy.yml
    prepare: prepare.yml
    converge: converge.yml
    verify: verify.yml

scenario:
  name: qemu
  test_sequence:
    - dependency
    - syntax
    - create
    - prepare
    - converge
    - verify
    - destroy

verifier:
  name: ansible
```

- [ ] **Step 2: Lifecycle one-liners**

`extensions/molecule/qemu/create.yml`:
```yaml
---
- name: Provision molecule instances
  import_playbook: david_igou.molecule_provisioners.create
```

`extensions/molecule/qemu/destroy.yml`:
```yaml
---
- name: Tear down molecule instances
  import_playbook: david_igou.molecule_provisioners.destroy
```

`extensions/molecule/qemu/prepare.yml`:
```yaml
---
- name: Prepare molecule instances
  import_playbook: david_igou.molecule_provisioners.prepare
```

- [ ] **Step 3: Converge + verify**

`extensions/molecule/qemu/converge.yml`:
```yaml
---
- name: Converge — verify SSH/exec works against every molecule host
  hosts: molecule
  gather_facts: true
  tasks:
    - name: Capture hostname and OS info
      ansible.builtin.debug:
        msg: "{{ ansible_hostname }} on {{ ansible_distribution }} {{ ansible_distribution_version }}"
```

`extensions/molecule/qemu/verify.yml`:
```yaml
---
- name: Verify — every molecule host responds to ping
  hosts: molecule
  gather_facts: false
  tasks:
    - name: Ping each host
      ansible.builtin.ping:
```

- [ ] **Step 4: Inventory with all three valid driver × network combos**

`extensions/molecule/qemu/inventory/hosts.yml`:
```yaml
all:
  children:
    molecule:
      hosts:
        ubuntu-libvirt-slirp:
          mp:
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              image_checksum: "sha256:REPLACE_AT_IMPL_TIME"
              driver: libvirt
              uri: qemu:///session
              network:
                mode: slirp
        ubuntu-process-slirp:
          mp:
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              image_checksum: "sha256:REPLACE_AT_IMPL_TIME"
              driver: process
              network:
                mode: slirp
        ubuntu-libvirt-nat:
          mp:
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              image_checksum: "sha256:REPLACE_AT_IMPL_TIME"
              driver: libvirt
              uri: qemu:///system   # NAT requires system URI; default network lives there
              network:
                mode: nat
```

`extensions/molecule/qemu/inventory/group_vars/molecule.yml`:
```yaml
---
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('qemu', true) }}"
mp_qemu_wait_timeout: 300   # TCG boot is slow

mp_defaults:
  qemu:
    cpus: 2
    memory: 1024
    ssh_user: ubuntu
```

- [ ] **Step 5: Smoke the scenario locally (only if qemu+libvirtd available on dev host)**

Run: `cd extensions/molecule/qemu && PROVISIONER=qemu molecule test --scenario-name qemu`
Expected: the two SLIRP hosts (`ubuntu-libvirt-slirp`, `ubuntu-process-slirp`) complete the full lifecycle. The `ubuntu-libvirt-nat` host is expected to fail in any environment that cannot bring up libvirt's `default` network (most container-based dev envs — `virbr0`/`dnsmasq` either isn't permitted or collides with `192.168.122.1`). **NAT is a CI-only merge gate**: GitHub Actions runs the NAT host on `ubuntu-latest` where the default network starts cleanly; local NAT failure is not a merge blocker. Skip the local smoke entirely if libvirtd is unreachable — CI covers all three hosts.

- [ ] **Step 6: Commit**

```bash
git add extensions/molecule/qemu/
git commit -m "test(qemu): self-test scenario exercising libvirt+slirp, process+slirp, libvirt+nat"
```

---

### Task 17: GitHub Actions integration-qemu job

**Files:**
- Modify: `.github/workflows/tests.yml`

- [ ] **Step 1: Read current workflow**

Run: `cat .github/workflows/tests.yml`

- [ ] **Step 2: Add `integration-qemu` job after `integration-kubevirt`**

Insert this block after the closing of the `integration-kubevirt` job (and before `all_green:`):

```yaml
  integration-qemu:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Checkout into the canonical collection path
        uses: actions/checkout@v4
        with:
          path: ansible_collections/david_igou/molecule_provisioners

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install qemu, libvirt, cloud-image-utils, and basic networking
        run: |
          sudo apt-get update
          sudo apt-get install -y \
            qemu-system-x86 qemu-utils \
            libvirt-daemon-system libvirt-clients \
            cloud-image-utils bridge-utils
          sudo systemctl enable --now libvirtd
          sudo virsh net-start default || true
          sudo virsh net-autostart default
          sudo usermod -a -G libvirt,kvm "$USER"

      - name: Install ansible + molecule + pytest plumbing
        working-directory: ansible_collections/david_igou/molecule_provisioners
        run: |
          python -m pip install --upgrade pip
          pip install ansible-core molecule \
                      pytest pytest-ansible pytest-xdist

      - name: Install collection dependencies
        working-directory: ansible_collections/david_igou/molecule_provisioners
        run: |
          ansible-galaxy collection install \
            containers.podman kubernetes.core community.crypto \
            'community.libvirt:>=1.3.0'

      - name: Cache base qcow2 images
        uses: actions/cache@v4
        with:
          path: ~/.cache/molecule-qemu
          # NOTE: bump the suffix when the pinned image URL or checksum changes.
          key: molecule-qemu-images-ubuntu-noble-v1

      - name: Run qemu scenario (TCG; /dev/kvm is unavailable on hosted runners)
        working-directory: ansible_collections/david_igou/molecule_provisioners
        env:
          PROVISIONER: qemu
          ANSIBLE_COLLECTIONS_PATH: ${{ github.workspace }}
        # `sg libvirt -c` re-execs the shell with the libvirt group active without
        # requiring a logout/login.
        run: |
          sg libvirt -c 'pytest tests/integration -v -k qemu -s -o addopts=""'

      - name: Collect libvirt diagnostics on failure
        if: failure()
        run: |
          sudo virsh list --all || true
          sudo virsh net-list --all || true
          sudo virsh net-dumpxml default || true
          sudo journalctl -u libvirtd --no-pager | tail -200 || true
```

- [ ] **Step 3: Add `integration-qemu` to `all_green` needs**

Modify the `all_green` job:
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
          '${{ needs.integration-qemu.result }}'
          ])"
```

- [ ] **Step 4: Lint the workflow**

Run: `yamllint .github/workflows/tests.yml`
Expected: no errors (or warnings about line length consistent with existing file).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci(qemu): add integration-qemu job with libvirtd + TCG"
```

---

## Phase 7 — Documentation + changelog

### Task 18: README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update "Supported backends" table**

In `README.md`, find the table at line 9-13 and replace with:
```markdown
| Backend | When to use |
| --- | --- |
| `podman` (default) | Containers, fastest CI loop |
| `kubevirt` | Real VMs in a Kubernetes cluster (requires KubeVirt) |
| `qemu` | Real VMs via local libvirtd or direct `qemu-system` process |
```

- [ ] **Step 2: Add qemu block to the `inventory/hosts.yml` example**

In the inventory example (lines 68-80), add a `qemu` sibling under `mp:` for the `ubuntu-24` host:
```yaml
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              driver: libvirt
              ssh_user: ubuntu
```

- [ ] **Step 3: Add qemu block to the `group_vars/molecule.yml` example**

In the defaults example (lines 82-95), add a `qemu` sibling under `mp_defaults`:
```yaml
  qemu:
    cpus: 2
    memory: 1024
    ssh_user: cloud-user
    network:
      mode: slirp
```

- [ ] **Step 4: Replace the "Out of scope" entry**

Change line 111 from:
```
- docker, qemu/libvirt, AWS, Azure, GCP backends
```
to:
```
- docker, AWS, Azure, GCP backends
- qemu/libvirt remote URIs and `network.mode: bridge` (planned for a later minor)
```

- [ ] **Step 5: Add "Controller-host prerequisites" subsection between "Using" and "What's in the box"**

```markdown
## Controller-host prerequisites by backend

| Backend | Required on the molecule controller |
| --- | --- |
| `podman` | `podman` |
| `kubevirt` | `kubectl` + a kubeconfig pointing at a KubeVirt-enabled cluster |
| `qemu` | `qemu-system-x86_64`, `qemu-img`, `cloud-localds` (or `genisoimage`); plus `libvirtd` reachable at the configured URI for `driver: libvirt` |
```

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: README updates for qemu backend"
```

---

### Task 19: MIGRATION.md updates

**Files:**
- Modify: `docs/MIGRATION.md`

- [ ] **Step 1: Remove qemu/libvirt from "What this collection does NOT support"**

In `docs/MIGRATION.md` line 143, change:
```
- Backends other than podman and kubevirt (no docker, qemu/libvirt, cloud).
```
to:
```
- Backends other than podman, kubevirt, and qemu (no docker, cloud providers).
- Remote libvirt URIs (`qemu+ssh://...`); `network.mode: bridge` for the qemu backend.
```

- [ ] **Step 2: Add a "Migrating from `molecule-plugins[libvirt]`" subsection after Step 5**

```markdown
## Migrating from `molecule-plugins[libvirt]`

The `molecule-plugins[libvirt]` driver expressed VMs as `platforms:` entries. This collection moves those fields into `mp.qemu.<field>` per host.

| `molecule-plugins[libvirt]` field | `mp.qemu.<field>` equivalent |
| --- | --- |
| `platforms[].box` / `image` | `mp.qemu.image` (URL to qcow2) |
| `platforms[].vcpus` | `mp.qemu.cpus` (integer) |
| `platforms[].memory` | `mp.qemu.memory` (integer MiB) |
| `platforms[].libvirt_user` / `connection` | `mp.qemu.ssh_user` |
| `platforms[].libvirt_host` | not supported in v1.1 (controller-local only) |
| `platforms[].networks[].name` | `mp.qemu.network.mode` (`slirp` or `nat`) |
| `driver.options.connection_uri` | `mp.qemu.uri` (per host) |

Mode-`bridge` networking is not in v1.1. If you used a bridge previously, either switch to `nat` (libvirt's `default` network) or wait for the bridge add-on.
```

- [ ] **Step 3: Commit**

```bash
git add docs/MIGRATION.md
git commit -m "docs(migration): add molecule-plugins[libvirt] field map"
```

---

### Task 20: Changelog fragment + release notes

**Files:**
- Create: `changelogs/fragments/qemu-backend.yml`

- [ ] **Step 1: Create the fragment**

`changelogs/fragments/qemu-backend.yml`:
```yaml
---
minor_changes:
  - >-
    Add ``qemu`` backend with per-host driver selection (``libvirt`` or
    ``process``) and SLIRP / libvirt-NAT networking. Selected via
    ``mp_backend: qemu`` (or ``PROVISIONER=qemu``); per-host shape under
    ``mp.qemu``. See ``docs/superpowers/specs/2026-05-21-qemu-backend-design.md``.
  - >-
    Add ``community.libvirt`` (``>=1.3.0``) as a galaxy dependency. Required only
    when ``mp.qemu.driver: libvirt`` is used.
```

- [ ] **Step 2: Verify with `antsibull-changelog`** (if available; otherwise skip)

Run: `antsibull-changelog lint 2>&1 | head` (only if installed)
Expected: no errors. Otherwise the upstream `changelog` CI job will validate.

- [ ] **Step 3: Commit**

```bash
git add changelogs/fragments/qemu-backend.yml
git commit -m "changelog: fragment for v1.1 qemu backend"
```

---

## Phase 8 — Final review

### Task 21: Local pre-PR check

- [ ] **Step 1: Run ansible-lint**

Run: `ansible-lint roles/qemu/ playbooks/ extensions/molecule/qemu/ 2>&1 | tail -30`
Expected: no errors. Fix any flagged issues inline; recommit per issue.

- [ ] **Step 2: Run yamllint**

Run: `yamllint roles/qemu/ extensions/molecule/qemu/ .github/workflows/tests.yml`
Expected: no errors.

- [ ] **Step 3: Run the full pytest suite**

Run: `pytest tests/integration/qemu/ -v`
Expected: validation tests + image-cache + seed-iso tests pass; the slow process-driver E2E either passes or is skipped if `qemu-system-x86_64` is missing.

- [ ] **Step 4: Run the existing podman scenario regression**

Run: `PROVISIONER=podman pytest tests/integration -v -k default`
Expected: still passes (no regression from the dispatcher change).

- [ ] **Step 5: Confirm `ansible-galaxy collection build` succeeds against the new version**

Run: `ansible-galaxy collection build --output-path /tmp/ . 2>&1 | tail -5`
Expected: produces `david_igou-molecule_provisioners-1.1.0.tar.gz`. Cleanup: `rm /tmp/david_igou-molecule_provisioners-1.1.0.tar.gz`.

- [ ] **Step 6: No commit (verification-only task).**

---

## Self-Review Findings

(Run by the plan author before handing off — captured here for the executor.)

1. **Spec coverage**:
   - Schema (driver, uri, cpus, memory, disk_size, ssh_user, network.mode, extra_args, image, image_checksum) → Task 4 (merge) + Task 5 (validation).
   - Image cache → Task 6.
   - Seed ISO build → Task 7.
   - Overlay (process + libvirt) → Tasks 8 and 12.
   - KVM/TCG detection → Task 9 step 2.
   - Process driver create/destroy → Tasks 9 and 10.
   - Runtime inventory → Task 11.
   - Libvirt driver create/destroy → Tasks 13 and 14.
   - NAT pre-reservation → Task 15.
   - Self-test scenario → Task 16.
   - CI job → Task 17.
   - Docs → Tasks 18, 19, 20.
   - Versioning bump → Task 2 (galaxy.yml).
   - Out-of-scope items (bridge, remote URIs, QMP plugin) — documented as future work in README/MIGRATION updates.

2. **Placeholder scan**: Two intentional placeholders are flagged:
   - `image_checksum: "sha256:REPLACE_AT_IMPL_TIME"` in `tests/integration/qemu/fixtures/process_slirp.yml` (Task 11) and `extensions/molecule/qemu/inventory/hosts.yml` (Task 16) — the executor MUST fetch the current sha256 from `https://cloud-images.ubuntu.com/noble/current/SHA256SUMS` at run time. This is not a plan defect; the checksum is timestamp-coupled.
   - The `<capacity unit='bytes'>2147483648</capacity>` placeholder in Task 12 (libvirt overlay) — 2 GiB is a deliberate lower-bound guess; a follow-up issue should parse the base image's actual capacity via `qemu-img info --output=json` and inject the result.

3. **Type consistency**:
   - `_mp_specs[host].driver`, `.network.mode`, `.image`, `.ssh_user`, `.cpus`, `.memory`, `.disk_size`, `.uri`, `.base_image_path`, `.overlay_path`, `.seed_iso_path`, `.mac`, `.ssh_host`, `.ssh_port`, `.pool_name` — used consistently across tasks. No method-rename drift.
   - `mp_qemu_kvm_ok` fact name used consistently in Tasks 9 (definition + process create) and 13 (domain XML template).
