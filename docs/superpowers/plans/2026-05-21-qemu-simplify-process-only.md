# qemu backend: simplify to process-driver only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Walk back the libvirt driver from the v1.1 qemu backend, leaving only the `process` driver with SLIRP networking. The libvirt path has surfaced four interacting bugs (virt_volume API, hardcoded emulator, libvirt 11.x portForward backend, devcontainer passt sandbox) — fixing them all is more work than the value justifies for v1.1. Process-driver only is the path that already works end-to-end locally and in CI, so collapse the design surface around it.

**Architecture:** Same `roles/qemu/` parallel to podman/kubevirt, but the role becomes a single-driver implementation: image cache → qcow2 overlay (`qemu-img create`) → NoCloud seed ISO → spawn `qemu-system-x86_64 -daemonize` with `-netdev user,hostfwd=...` → write runtime inventory. No libvirt module calls, no domain XML template, no libvirt-NAT path, no driver-dispatch indirection. Schema collapses to `mp.qemu.{image, image_checksum?, cpus?, memory?, disk_size?, ssh_user?, host_port?, extra_args?}` — no `driver`, no `uri`, no `network`. `community.libvirt` drops out of the galaxy deps.

**Tech Stack:** Ansible 2.15+, `community.crypto` (already a dep), `containers.podman`/`kubernetes.core` (other backends, untouched). Host-tool prereqs: `qemu-system-x86_64`, `qemu-img`, `cloud-localds` or `genisoimage`. No libvirtd. No `community.libvirt`.

**Scope note:** This plan rewinds spec language already shipped on the branch (`6d1426d docs(qemu): NAT is a CI-only merge gate`) — once libvirt is gone, NAT goes with it, so that paragraph is also walked back. The plan also deletes a sibling plan file (`docs/superpowers/plans/2026-05-21-qemu-libvirt-slirp-fixes.md`) that was written before this scope shift.

---

## File map

**Delete:**
- `roles/qemu/tasks/_create_libvirt.yml`
- `roles/qemu/tasks/_destroy_libvirt.yml`
- `roles/qemu/templates/domain.xml.j2`
- `tests/integration/qemu/fixtures/bad_driver.yml`
- `tests/integration/qemu/fixtures/process_nat_invalid.yml`
- `docs/superpowers/plans/2026-05-21-qemu-libvirt-slirp-fixes.md`
- `extensions/molecule/qemu/` — the entire directory. Qemu plugs into the existing `extensions/molecule/default/` scenario like podman and kubevirt do; no separate scenario.

**Rewrite (significant):**
- `roles/qemu/tasks/create.yml` — drop libvirt dispatch + KVM-detection-for-libvirt; keep image-cache → seed-ISO → overlay → process-launch → runtime-inventory flow.
- `roles/qemu/tasks/destroy.yml` — drop libvirt dispatch + NAT-reservation-removal + pool teardown; keep process-destroy + cidata cleanup.
- `roles/qemu/tasks/_overlay.yml` — keep only the process-driver `qemu-img create` + optional `qemu-img resize`; delete the libvirt transient-pool + virt_volume sections.
- `roles/qemu/tasks/_validate.yml` — drop driver / network.mode / driver+network compatibility assertions; keep `image` non-empty + cache-dir-writable.
- `roles/qemu/tasks/_create_process.yml` — drop the `when: _mp_specs[item].driver == 'process'` guards on every task (only one driver now).
- `roles/qemu/tasks/_destroy_process.yml` — same.
- `roles/qemu/defaults/main.yml` — drop `mp_qemu_allowed_drivers`, `mp_qemu_allowed_network_modes`, the `driver`/`uri`/`network` keys inside `mp_qemu_role_defaults`.
- `roles/qemu/tasks/_spec_merge.yml` — unchanged structurally, but the merge defaults are smaller.
- `tests/integration/qemu/test_qemu_unit.py` — drop `test_bad_driver_fails_with_message`, `test_process_nat_combo_fails_with_message`; keep merge/validation/image-cache/seed-iso/destroy/process-E2E tests (validation test fixtures get adjusted).
- `tests/integration/qemu/fixtures/process_slirp.yml` — drop `driver:` and `network:` keys; rename file to `process.yml` for clarity. *(All references in test_qemu_unit.py and assertions/* are updated.)*
- `tests/integration/qemu/fixtures/valid_minimal.yml`, `valid_local_image.yml`, `missing_image.yml` — drop any `driver` / `network.mode` keys; `valid_minimal.yml`'s `h-overrides` host loses its `network.mode: nat` override (replace with a different override, e.g. `cpus: 4`).
- `tests/integration/qemu/assertions/run_validate.yml` — drop assertions that reference removed driver/network keys.
- `extensions/molecule/default/inventory/hosts.yml` — add a `mp.qemu:` block to the `instance` host (alongside the existing `mp.podman:` and `mp.kubevirt:` blocks).
- `extensions/molecule/default/inventory/group_vars/molecule.yml` — add a `mp_defaults.qemu` block alongside `mp_defaults.podman` and `mp_defaults.kubevirt`. Add `mp_qemu_wait_timeout: 300` for slow TCG boot.
- `.github/workflows/tests.yml` (`integration-qemu` job) — drop libvirt-daemon-system / libvirt-clients / bridge-utils install, drop `virsh net-*` calls, drop `community.libvirt:>=1.3.0` from `ansible-galaxy collection install`, drop `sg libvirt -c` wrapper, drop libvirt diagnostics block on failure. Change the pytest selector from `-k qemu` to `-k default` (qemu now shares the default scenario with the other two backends; the `PROVISIONER=qemu` env var picks the backend).
- `galaxy.yml` — drop `community.libvirt: ">=1.3.0"` from `dependencies`. Update `description` to remove libvirt.
- `changelogs/fragments/qemu-backend.yml` — rewrite to reflect process-only + slirp-only.
- `README.md` — remove the libvirt-related text in the qemu row, qemu inventory snippet, qemu defaults block, and controller-host prereqs table.
- `docs/MIGRATION.md` — drop the entire "Migrating from `molecule-plugins[libvirt]`" subsection; update the "Backends out of scope" paragraph to add `qemu/libvirt`.
- `docs/superpowers/specs/2026-05-21-qemu-backend-design.md` — wholesale rewrite of Architecture, Solution overview, Inventory schema, Lifecycle: create, Lifecycle: destroy, Error handling, Testing, and Documentation sections to drop all libvirt content.
- `docs/superpowers/plans/2026-05-21-qemu-backend.md` — prepend a `> **SUPERSEDED**` note pointing here; do not edit the body (it stands as the historical trail).

**Untouched:**
- `roles/qemu/tasks/_image_cache.yml`, `_seed_iso.yml`, `_seed_iso_host.yml`, `_runtime_inventory.yml`, `main.yml`, `prepare.yml` — driver-agnostic.
- `roles/qemu/templates/user-data.j2`, `meta-data.j2` — driver-agnostic.
- `playbooks/{create,destroy,prepare}.yml`, `playbooks/group_vars/all.yml` — dispatcher already shape-correct (it just reads `mp_backend`).
- `roles/podman/`, `roles/kubevirt/` — unrelated.

---

## Pre-flight

From the worktree (`.claude/worktrees/feat-qemu-backend`):

```bash
git log --oneline -5
```

Expected top commit: `6d1426d docs(qemu): NAT is a CI-only merge gate, not a local requirement` (the reset point established before this plan starts). If the top commit is something else, stop and reconcile — this plan assumes the libvirt-fix commits and the libvirt-slirp test scaffolding are no longer in history.

Ensure the canonical-path symlink + dependencies:

```bash
mkdir -p "$HOME/.ansible/collections/ansible_collections/david_igou"
ln -snf "$PWD" "$HOME/.ansible/collections/ansible_collections/david_igou/molecule_provisioners"
ansible-galaxy collection list | grep -E "david_igou.molecule_provisioners|community.crypto"
```

Expected: `david_igou.molecule_provisioners 1.1.0` and `community.crypto 3.x` listed. (`community.libvirt` may also be listed — fine, this plan won't use it.)

---

## Phase 1 — Strip role of libvirt

### Task 1: Delete libvirt-only role files

**Files:**
- Delete: `roles/qemu/tasks/_create_libvirt.yml`
- Delete: `roles/qemu/tasks/_destroy_libvirt.yml`
- Delete: `roles/qemu/templates/domain.xml.j2`

- [ ] **Step 1: Delete the three files**

```bash
git rm roles/qemu/tasks/_create_libvirt.yml \
       roles/qemu/tasks/_destroy_libvirt.yml \
       roles/qemu/templates/domain.xml.j2
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor(qemu): drop libvirt-driver task and template files"
```

---

### Task 2: Strip create.yml of libvirt + KVM-detection-for-libvirt

**Files:**
- Modify: `roles/qemu/tasks/create.yml`

The current file (around 50 lines) routes through validation → image cache → seed ISO → overlay → KVM detection → process create → libvirt create → runtime inventory. Once libvirt is gone, KVM detection (`_mp_qemu_kvm_ok`) is still consumed by `_create_process.yml` (as `_accel: "{{ 'kvm:tcg' if _mp_qemu_kvm_ok else 'tcg' }}"`), so it stays. Just drop the libvirt phase.

- [ ] **Step 1: Replace the contents of `roles/qemu/tasks/create.yml`**

Write `roles/qemu/tasks/create.yml`:

```yaml
---
# Phase 1: build spec map (shared between create and destroy).
- name: Merge per-host specs
  ansible.builtin.include_tasks: _spec_merge.yml

# Phase 2: validate. Fail fast before any side effects.
- name: Validate per-host specs
  ansible.builtin.include_tasks: _validate.yml

- name: Ensure image cache dir exists and is writable
  ansible.builtin.file:
    path: "{{ mp_qemu_image_cache_dir }}"
    state: directory
    mode: "0755"

# Phase 3: download and cache base qcow2 images (idempotent).
- name: Cache base images
  ansible.builtin.include_tasks: _image_cache.yml

# Phase 4: build per-host NoCloud seed ISOs.
- name: Build seed ISOs
  ansible.builtin.include_tasks: _seed_iso.yml

# Phase 5: per-host qcow2 overlay (process driver only).
- name: Create per-VM overlays
  ansible.builtin.include_tasks: _overlay.yml

# Phase 6: detect KVM availability once (consumed by _create_process.yml).
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

# Phase 7: launch the VM process per host.
- name: Launch process-driver VMs
  ansible.builtin.include_tasks: _create_process.yml

# Phase 8: write runtime connection inventory for the molecule prepare phase.
- name: Write runtime inventory
  ansible.builtin.include_tasks: _runtime_inventory.yml
```

- [ ] **Step 2: Commit**

```bash
git add roles/qemu/tasks/create.yml
git commit -m "refactor(qemu): drop libvirt-driver branch from create.yml"
```

---

### Task 3: Strip destroy.yml of libvirt + NAT + pool teardown

**Files:**
- Modify: `roles/qemu/tasks/destroy.yml`

Current file: spec merge → process destroy (when driver==process) → libvirt destroy (when driver==libvirt) → NAT static reservation removal → pool teardown → cidata cleanup. Drop everything but spec merge → process destroy → cidata cleanup.

- [ ] **Step 1: Replace the contents of `roles/qemu/tasks/destroy.yml`**

Write `roles/qemu/tasks/destroy.yml`:

```yaml
---
- name: Merge per-host specs (defensive)
  ansible.builtin.include_tasks: _spec_merge.yml

- name: Destroy process-driver VMs
  ansible.builtin.include_tasks: _destroy_process.yml
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: Remove cidata directories
  ansible.builtin.file:
    path: "{{ molecule_ephemeral_directory }}/{{ item }}-cidata"
    state: absent
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 2: Commit**

```bash
git add roles/qemu/tasks/destroy.yml
git commit -m "refactor(qemu): drop libvirt + NAT + pool teardown from destroy.yml"
```

---

### Task 4: Strip _overlay.yml of libvirt sections

**Files:**
- Modify: `roles/qemu/tasks/_overlay.yml`

Current file: process-overlay (`qemu-img create`) + optional resize + libvirt transient pool + virt_volume + libvirt resize + record overlay path. Drop libvirt sections, keep process overlay + resize + path record.

- [ ] **Step 1: Replace the contents of `roles/qemu/tasks/_overlay.yml`**

Write `roles/qemu/tasks/_overlay.yml`:

```yaml
---
# Process-driver overlay: qemu-img create the per-host qcow2 backed by the cached base.
- name: "Create qcow2 overlay for {{ item }}"
  ansible.builtin.command:
    cmd: >-
      qemu-img create -f qcow2
      -F qcow2 -b {{ _mp_specs[item].base_image_path }}
      {{ molecule_ephemeral_directory }}/{{ item }}.qcow2
    creates: "{{ molecule_ephemeral_directory }}/{{ item }}.qcow2"
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"

- name: "Resize overlay for {{ item }} (if disk_size set)"
  ansible.builtin.command:
    cmd: "qemu-img resize {{ molecule_ephemeral_directory }}/{{ item }}.qcow2 {{ _mp_specs[item].disk_size }}"
  when:
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

- [ ] **Step 2: Commit**

```bash
git add roles/qemu/tasks/_overlay.yml
git commit -m "refactor(qemu): drop libvirt pool/volume branches from _overlay.yml"
```

---

### Task 5: Drop driver/network conditionals from _create_process.yml + _destroy_process.yml

**Files:**
- Modify: `roles/qemu/tasks/_create_process.yml`
- Modify: `roles/qemu/tasks/_destroy_process.yml`

Both files currently gate their tasks with `when: _mp_specs[item].driver == 'process'`. With only one driver, those guards are dead code.

- [ ] **Step 1: Edit `_create_process.yml`** — remove the two `when: _mp_specs[item].driver == 'process'` lines (one on the launch task, one on the ssh_port record task)

The launch task block ends with:

```yaml
    creates: "{{ molecule_ephemeral_directory }}/{{ item }}.pid"
  when: _mp_specs[item].driver == 'process'
  loop: "{{ groups['molecule'] }}"
```

Change to:

```yaml
    creates: "{{ molecule_ephemeral_directory }}/{{ item }}.pid"
  loop: "{{ groups['molecule'] }}"
```

The ssh_port record task block ends with:

```yaml
         })
       }}
  when: _mp_specs[item].driver == 'process'
  loop: "{{ groups['molecule'] }}"
```

Change to:

```yaml
         })
       }}
  loop: "{{ groups['molecule'] }}"
```

- [ ] **Step 2: Edit `_destroy_process.yml`** — no per-task `when: driver == process` guards exist in this file (destroy is per-host include_tasks under a loop in destroy.yml). Verify by reading the file; if any guard exists, drop it.

Run:

```bash
grep -n "driver" roles/qemu/tasks/_destroy_process.yml
```

Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add roles/qemu/tasks/_create_process.yml roles/qemu/tasks/_destroy_process.yml
git commit -m "refactor(qemu): drop driver-conditional guards from process tasks"
```

---

### Task 6: Shrink _validate.yml + defaults/main.yml

**Files:**
- Modify: `roles/qemu/tasks/_validate.yml`
- Modify: `roles/qemu/defaults/main.yml`

- [ ] **Step 1: Replace `roles/qemu/tasks/_validate.yml`**

Write `roles/qemu/tasks/_validate.yml`:

```yaml
---
# Fail-fast validation. Run after _spec_merge.yml. No side effects.
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
```

- [ ] **Step 2: Replace `roles/qemu/defaults/main.yml`**

Write `roles/qemu/defaults/main.yml`:

```yaml
---
# Defaults for david_igou.molecule_provisioners.qemu (process driver, slirp networking).

# SSH keypair lives in the molecule ephemeral dir so destroy can find it.
mp_qemu_ssh_key_path: "{{ molecule_ephemeral_directory }}/identity_file"

# wait_for_connection timeout (seconds) for prepare phase.
# Boot under TCG is much slower than KVM — keep generous.
mp_qemu_wait_timeout: 180

# Base port for SLIRP host-forwards. ansible_port = base + host index.
mp_qemu_slirp_port_base: 2222

# Image cache root. Honours XDG_CACHE_HOME, falls back to ~/.cache/molecule-qemu.
mp_qemu_image_cache_dir: >-
  {{ (lookup('env', 'XDG_CACHE_HOME') | default(lookup('env', 'HOME') ~ '/.cache', true))
     ~ '/molecule-qemu' }}

# Per-host field defaults. Layered as: this dict <- mp_defaults.qemu <- hostvars[item].mp.qemu.
# Only `image` is required and is therefore absent from this dict.
mp_qemu_role_defaults:
  cpus: 2
  memory: 1024
  ssh_user: cloud-user
```

- [ ] **Step 3: Commit**

```bash
git add roles/qemu/tasks/_validate.yml roles/qemu/defaults/main.yml
git commit -m "refactor(qemu): collapse schema validation + defaults to process+slirp shape"
```

---

## Phase 2 — Strip tests of libvirt

### Task 7: Drop obsolete fixtures and assertions

**Files:**
- Delete: `tests/integration/qemu/fixtures/bad_driver.yml`
- Delete: `tests/integration/qemu/fixtures/process_nat_invalid.yml`
- Modify: `tests/integration/qemu/fixtures/valid_minimal.yml`
- Modify: `tests/integration/qemu/fixtures/process_slirp.yml` → rename to `process.yml`
- Modify: `tests/integration/qemu/fixtures/valid_local_image.yml`
- Modify: `tests/integration/qemu/fixtures/missing_image.yml` (sanity check only — likely already minimal)
- Modify: `tests/integration/qemu/assertions/run_validate.yml`

- [ ] **Step 1: Delete the two obsolete fixtures**

```bash
git rm tests/integration/qemu/fixtures/bad_driver.yml \
       tests/integration/qemu/fixtures/process_nat_invalid.yml
```

- [ ] **Step 2: Rewrite `valid_minimal.yml`**

Write `tests/integration/qemu/fixtures/valid_minimal.yml`:

```yaml
---
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
  vars:
    mp_backend: qemu
    mp_defaults:
      qemu:
        ssh_user: ubuntu
```

- [ ] **Step 3: Rename + simplify the process fixture**

```bash
git mv tests/integration/qemu/fixtures/process_slirp.yml \
       tests/integration/qemu/fixtures/process.yml
```

Write `tests/integration/qemu/fixtures/process.yml`:

```yaml
---
all:
  children:
    molecule:
      hosts:
        ubuntu-qemu:
          mp:
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              image_checksum: "sha256:6e7016f2c9f4d3c00f48789eb6b9043ba2172ccc1b6b1eaf3ed1e29dd3e52bb3"
              memory: 1024
              cpus: 2
              ssh_user: ubuntu
  vars:
    mp_backend: qemu
    mp_defaults:
      qemu:
        ssh_user: ubuntu
```

(`image_checksum` stays pinned to the same Ubuntu Noble cloud image sha256; refresh from `https://cloud-images.ubuntu.com/noble/current/SHA256SUMS` if Canonical re-publishes.)

- [ ] **Step 4: Verify `valid_local_image.yml` + `missing_image.yml`**

Run:

```bash
grep -nE "driver|network" tests/integration/qemu/fixtures/valid_local_image.yml \
                          tests/integration/qemu/fixtures/missing_image.yml
```

Expected: no matches. (`valid_local_image.yml` already uses only `image: file://...`; `missing_image.yml` defines a host without `image`.) If matches appear, strip the offending keys.

- [ ] **Step 5: Update `assertions/run_validate.yml`**

The current file likely asserts on driver / network.mode. Rewrite it as a minimal harness that just runs the spec-merge + validation tasks (which will surface the only remaining assertion: `image` non-empty). Write `tests/integration/qemu/assertions/run_validate.yml`:

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

    - name: Validate specs
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../../../../roles/qemu/tasks/_validate.yml"
```

- [ ] **Step 6: Commit**

```bash
git add tests/integration/qemu/fixtures/ tests/integration/qemu/assertions/run_validate.yml
git commit -m "test(qemu): drop driver/network fixtures, rename process_slirp -> process"
```

---

### Task 8: Rewrite test_qemu_unit.py

**Files:**
- Modify: `tests/integration/qemu/test_qemu_unit.py`

Drop `test_bad_driver_fails_with_message` and `test_process_nat_combo_fails_with_message`. Rename `test_process_driver_e2e` to use the renamed fixture `process.yml`. (`test_libvirt_driver_e2e` was reset out of history in the pre-flight step.)

- [ ] **Step 1: Edit `tests/integration/qemu/test_qemu_unit.py`**

Replace the two failing assertions block with the surviving tests. The complete contents of `tests/integration/qemu/test_qemu_unit.py` should be:

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


def test_missing_image_fails_with_message() -> None:
    proc = _run("run_validate.yml", "missing_image.yml")
    assert proc.returncode != 0
    assert "is missing qemu.image" in proc.stdout


def test_image_cache_creates_cached_file() -> None:
    proc = _run("run_image_cache.yml", "valid_local_image.yml")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_seed_iso_is_built_and_contains_user(tmp_path) -> None:
    import os
    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "valid_local_image.yml"),
         str(ASSERTIONS / "run_seed_iso.yml")],
        cwd=COLLECTION_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_destroy_is_idempotent_on_fresh_state(tmp_path) -> None:
    import os
    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "valid_local_image.yml"),
         str(ASSERTIONS / "run_destroy.yml")],
        cwd=COLLECTION_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.slow
def test_process_driver_e2e(tmp_path) -> None:
    import os
    import shutil
    if not shutil.which("qemu-system-x86_64"):
        pytest.skip("qemu-system-x86_64 not installed")
    env = os.environ.copy()
    env["MOLECULE_EPHEMERAL_DIRECTORY"] = str(tmp_path)
    proc = subprocess.run(
        ["ansible-playbook", "-i", str(FIXTURES / "process.yml"),
         str(ASSERTIONS / "run_process_e2e.yml")],
        cwd=COLLECTION_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Run the fast tests**

```bash
python3 -m pytest tests/integration/qemu/test_qemu_unit.py -v -k "not slow" -o addopts="" 2>&1 | tail -15
```

Expected: 4 pass, 1 deselected (the slow E2E). If `test_destroy_is_idempotent_on_fresh_state` fails with a `community.libvirt` lookup error, that's the pre-existing env issue — not a blocker for this plan (the `destroy.yml` rewrite in Task 3 no longer references community.libvirt at all, so the failure mode goes away once that change is in this branch's history). If it still fails after Task 3, debug then. **Expected here: it passes**, because destroy.yml no longer imports community.libvirt.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/qemu/test_qemu_unit.py
git commit -m "test(qemu): drop libvirt + driver-validation unit tests; rename fixture"
```

---

## Phase 3 — Strip the self-test scenario + CI

### Task 9: Fold qemu into the existing `default` scenario; drop the qemu-specific scenario

**Files:**
- Delete: `extensions/molecule/qemu/` (entire directory)
- Modify: `extensions/molecule/default/inventory/hosts.yml`
- Modify: `extensions/molecule/default/inventory/group_vars/molecule.yml`

The collection's contract is "one scenario, switch backends with `PROVISIONER=`". Qemu should plug into the existing `extensions/molecule/default/` scenario alongside podman and kubevirt — no separate scenario, no second molecule.yml, one host file with three `mp.<backend>:` blocks.

- [ ] **Step 1: Delete the qemu-only scenario directory**

```bash
git rm -r extensions/molecule/qemu/
```

- [ ] **Step 2: Add an `mp.qemu:` block to the default scenario's instance host**

Replace `extensions/molecule/default/inventory/hosts.yml` with:

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
```

(The `image_checksum` is pinned; refresh from `https://cloud-images.ubuntu.com/noble/current/SHA256SUMS` if Canonical re-publishes the Ubuntu Noble cloud image.)

- [ ] **Step 3: Add `mp_defaults.qemu` to the default scenario's group_vars**

Replace `extensions/molecule/default/inventory/group_vars/molecule.yml` with:

```yaml
---
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"
# Bump from the role default of 180s — TCG emulation in CI takes longer for VMs to boot.
mp_kubevirt_wait_timeout: 300
mp_qemu_wait_timeout: 300   # TCG boot under qemu is similarly slow on hosted runners.

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
```

(Note `ssh_user: ubuntu` for qemu — the Ubuntu cloud image's default user. podman and kubevirt keep their existing defaults.)

- [ ] **Step 4: Verify the converge.yml/verify.yml at the default scenario are backend-agnostic**

```bash
cat extensions/molecule/default/converge.yml extensions/molecule/default/verify.yml
```

Expected: converge gathers facts + debug-prints; verify pings. Both work for any Linux guest. If either is OS-specific (Fedora-only, dnf-based, etc.), file a separate follow-up — but the current shipped versions are already OS-agnostic.

- [ ] **Step 5: Commit**

```bash
git add extensions/molecule/default/ extensions/molecule/qemu/
git commit -m "test(qemu): fold into the existing default scenario alongside podman/kubevirt"
```

---

### Task 10: Simplify the `integration-qemu` CI job

**Files:**
- Modify: `.github/workflows/tests.yml`

- [ ] **Step 1: Edit the `integration-qemu` job block**

Locate the `integration-qemu:` job in `.github/workflows/tests.yml` (currently around line 158-221). Replace the body with the slimmed version below (everything between `integration-qemu:` and the next job's `:` line):

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

      - name: Install qemu + cloud-image-utils
        run: |
          sudo apt-get update
          sudo apt-get install -y qemu-system-x86 qemu-utils cloud-image-utils

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
            containers.podman kubernetes.core community.crypto

      - name: Cache base qcow2 images
        uses: actions/cache@v4
        with:
          path: ~/.cache/molecule-qemu
          # NOTE: bump the suffix when the pinned image URL or checksum changes.
          key: molecule-qemu-images-ubuntu-noble-v1

      - name: Run default scenario under PROVISIONER=qemu (TCG; /dev/kvm is unavailable on hosted runners)
        working-directory: ansible_collections/david_igou/molecule_provisioners
        env:
          PROVISIONER: qemu
          ANSIBLE_COLLECTIONS_PATH: ${{ github.workspace }}
        run: |
          pytest tests/integration -v -k default -s -o addopts=""
```

(Deletes the libvirt/bridge apt-get list, the `virsh net-start`/`net-autostart`/`usermod` lines, the `community.libvirt:>=1.3.0` install, the `sg libvirt -c` wrapper, and the failure-time libvirt diagnostics block.)

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci(qemu): drop libvirt apparatus from integration-qemu job"
```

---

## Phase 4 — Tone down spec + docs + galaxy

### Task 11: Drop community.libvirt from galaxy.yml and update description

**Files:**
- Modify: `galaxy.yml`

- [ ] **Step 1: Edit the `dependencies` and `description`**

In `galaxy.yml`:

Change:

```yaml
description: >-
  Reusable Molecule provisioner playbooks and roles (podman, kubevirt, qemu) for
  testing other Ansible collections without copy-pasting create/destroy/prepare
  automation per repo.
```

(Description is fine — qemu remains a backend, just narrower in scope. Leave the description as-is.)

Change:

```yaml
dependencies:
  containers.podman: ">=1.10.0"
  kubernetes.core: ">=3.0.0"
  community.crypto: ">=2.0.0"
  community.libvirt: ">=1.3.0"
```

to:

```yaml
dependencies:
  containers.podman: ">=1.10.0"
  kubernetes.core: ">=3.0.0"
  community.crypto: ">=2.0.0"
```

- [ ] **Step 2: Commit**

```bash
git add galaxy.yml
git commit -m "build(qemu): drop community.libvirt dep now that libvirt driver is gone"
```

---

### Task 12: Rewrite the changelog fragment

**Files:**
- Modify: `changelogs/fragments/qemu-backend.yml`

- [ ] **Step 1: Replace the file**

Write `changelogs/fragments/qemu-backend.yml`:

```yaml
---
minor_changes:
  - >-
    Add ``qemu`` backend. Spawns a local ``qemu-system-x86_64 -daemonize``
    per host, with cloud-init NoCloud seed-ISO bootstrap and SLIRP user-mode
    networking (SSH reachable on the controller at
    ``127.0.0.1:<mp_qemu_slirp_port_base + host index>``). Selected via
    ``mp_backend: qemu`` (or ``PROVISIONER=qemu``); per-host shape under
    ``mp.qemu.{image, image_checksum?, cpus?, memory?, disk_size?, ssh_user?,
    host_port?, extra_args?}``. See
    ``docs/superpowers/specs/2026-05-21-qemu-backend-design.md``.
```

- [ ] **Step 2: Commit**

```bash
git add changelogs/fragments/qemu-backend.yml
git commit -m "changelog: rewrite qemu fragment for process+slirp-only v1.1"
```

---

### Task 13: README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the "Supported backends" row for qemu**

In `README.md`, locate the table row:

```markdown
| `qemu` | Real VMs via local libvirtd or direct `qemu-system` process |
```

Change to:

```markdown
| `qemu` | Real VMs via direct `qemu-system` process (no libvirtd) |
```

- [ ] **Step 2: Update the inventory snippet**

Locate the `qemu:` block in the hosts.yml snippet:

```yaml
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              driver: libvirt
              ssh_user: ubuntu
```

Change to:

```yaml
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              ssh_user: ubuntu
```

- [ ] **Step 3: Update the `mp_defaults.qemu` block**

Locate:

```yaml
  qemu:
    cpus: 2
    memory: 1024
    ssh_user: cloud-user
    network:
      mode: slirp
```

Change to:

```yaml
  qemu:
    cpus: 2
    memory: 1024
    ssh_user: cloud-user
```

- [ ] **Step 4: Update the controller-host prereqs row**

Locate:

```markdown
| `qemu` | `qemu-system-x86_64`, `qemu-img`, `cloud-localds` (or `genisoimage`); plus `libvirtd` reachable at the configured URI for `driver: libvirt` |
```

Change to:

```markdown
| `qemu` | `qemu-system-x86_64`, `qemu-img`, `cloud-localds` (or `genisoimage`) |
```

- [ ] **Step 5: Update the "Out of scope" list**

In the existing list:

```markdown
- qemu/libvirt remote URIs and `network.mode: bridge` (planned for a later minor)
```

Replace with:

```markdown
- qemu via libvirtd (use the `process` path that ships, or a future minor)
- qemu remote / non-controller-local hosts
- qemu NAT or bridge networking (SLIRP only in v1.1)
```

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs(qemu): README reflects process+slirp-only v1.1 surface"
```

---

### Task 14: MIGRATION.md updates

**Files:**
- Modify: `docs/MIGRATION.md`

- [ ] **Step 1: Delete the `## Migrating from molecule-plugins[libvirt]` section**

In `docs/MIGRATION.md`, delete the subsection that starts at the line `## Migrating from \`molecule-plugins[libvirt]\`` and continues through (and including) the table + the "Mode-`bridge` networking is not in v1.1..." paragraph. This is the block roughly between lines 141 and 156.

- [ ] **Step 2: Update the "Out of scope" paragraph**

Locate the line:

```markdown
- Remote libvirt URIs (`qemu+ssh://...`); `network.mode: bridge` for the qemu backend.
```

Replace with:

```markdown
- qemu via libvirtd, qemu+ssh remote URIs, NAT/bridge networking — v1.1 only ships the direct-process driver with SLIRP networking.
```

- [ ] **Step 3: Commit**

```bash
git add docs/MIGRATION.md
git commit -m "docs(qemu): drop molecule-plugins[libvirt] migration section"
```

---

### Task 15: Rewrite the design spec

**Files:**
- Modify: `docs/superpowers/specs/2026-05-21-qemu-backend-design.md`

The spec currently runs ~272 lines covering both drivers + NAT. Replace the whole body (keep only the front-matter title/status/date/release line) with the process-only design.

- [ ] **Step 1: Replace the file contents entirely**

Write `docs/superpowers/specs/2026-05-21-qemu-backend-design.md`:

```markdown
# `david_igou.molecule_provisioners` — qemu backend (v1.1)

**Status:** Approved (scaled down from the original libvirt+process design after local libvirt-driver verification surfaced multiple interacting bugs not worth fixing in v1.1)
**Date:** 2026-05-21
**Target release:** v1.1.0

## Problem

v1.0 ships podman and kubevirt backends. Consumers who want real VMs without a Kubernetes cluster — homelab KVM hosts, GitHub-hosted runners that just have qemu installed — have no fit.

The goal: add a `qemu` backend that gives consumers a real VM via the smallest possible host-prereq surface.

## Solution overview

A new role `roles/qemu/`, dispatched by `mp_backend: qemu` at the top level. The role spawns `qemu-system-x86_64 -daemonize` directly — no `libvirtd` dependency, no `community.libvirt` collection dependency. SSH reaches the VM via SLIRP user-mode networking with a host-side port forward (`-netdev user,id=net0,hostfwd=tcp::<port>-:22`).

Shared concerns — base-image download + cache, qcow2 overlay, NoCloud cloud-init seed ISO, runtime-inventory write-back — sit alongside the process-launch step. No driver dispatch.

### What this design _doesn't_ ship in v1.1

- **libvirt driver.** The original v1.1 design included a `mp.qemu.driver: libvirt|process` switch. During local verification we surfaced four interacting bugs (`community.libvirt.virt_volume` `command: create` dispatch, hardcoded `<emulator>` path, libvirt 11.x's mandatory `<backend type='passt'/>` for `<portForward>`, and devcontainer-level passt-sandbox `/proc` access) that together made the libvirt path more cost than value for v1.1. Removed entirely; can come back in a future minor if needed.
- **NAT networking.** Was tied to the libvirt driver (libvirt's `default` network + `<host>` reservation). Gone with libvirt.
- **Remote URIs, bridge networking, Windows guests, non-cloud-init images.** Same as before — not in v1.1.

## Architecture

```
roles/qemu/
├── defaults/main.yml             mp_qemu_role_defaults + image cache dir + SLIRP port base
├── meta/main.yml                 role metadata
├── tasks/
│   ├── main.yml                  tasks_from dispatcher (mirrors other roles)
│   ├── create.yml                merge → validate → cache → seed → overlay → KVM detect → launch → write inventory
│   ├── destroy.yml               merge → process destroy (per host) → cidata cleanup
│   ├── prepare.yml               wait_for_connection (mirrors kubevirt's prepare)
│   ├── _spec_merge.yml           3-level merge: role_defaults <- mp_defaults.qemu <- hostvars[item].mp.qemu
│   ├── _validate.yml             one assertion: `image` is set non-empty per host
│   ├── _image_cache.yml          get_url base qcow2 into XDG cache, keyed by sha256(url)
│   ├── _overlay.yml              qemu-img create -f qcow2 -b <base>; optional qemu-img resize
│   ├── _seed_iso.yml             render NoCloud user-data + meta-data, build seed.iso via cloud-localds | genisoimage
│   ├── _seed_iso_host.yml        per-host helper for _seed_iso.yml
│   ├── _create_process.yml       build qemu-system argv, launch with --daemonize --pidfile, record pid + ssh facts
│   ├── _destroy_process.yml      slurp pidfile → kill -TERM → wait_for absent → file absent on artifacts
│   └── _runtime_inventory.yml    build __mp_qemu_runtime_hosts; write molecule_runtime.yml
└── templates/
    ├── user-data.j2              cloud-init NoCloud user-data
    └── meta-data.j2              cloud-init NoCloud meta-data
```

**Dispatcher delta**: `playbooks/group_vars/all.yml` adds `qemu` to `mp_supported_backends`. The dispatcher's per-host validation (`hostvars[item].mp[_mp_backend] is defined`) is already shape-correct — no changes to `playbooks/{create,destroy,prepare}.yml`.

### Why module-first

Almost all role steps go through Ansible modules: `ansible.builtin.get_url`, `ansible.builtin.file`, `ansible.builtin.template`, `ansible.builtin.set_fact`, `community.crypto.openssh_keypair`, `ansible.builtin.wait_for`. Three shell-outs remain, each unavoidable in v1.1:

1. **`qemu-img create` / `qemu-img resize`** for the overlay (no first-class Ansible module for qcow2 backing files).
2. **`cloud-localds`** (or `genisoimage` fallback) for NoCloud ISO build (no Ansible module produces a NoCloud-format ISO).
3. **`kill -TERM` + `wait_for` (path absent)** for shutdown. A QMP action plugin is a candidate for a later minor; filed as a follow-up.

## Inventory schema

**`inventory/hosts.yml` — per-host shape** (required field + optional fields shown):

```yaml
all:
  children:
    molecule:
      hosts:
        ubuntu-24:
          mp:
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              image_checksum: "sha256:abcd…"     # forwarded to get_url; optional
              cpus: 2                            # default 2
              memory: 1024                       # MiB, integer; default 1024
              disk_size: 10G                     # passed to qemu-img resize; null = no resize
              ssh_user: ubuntu                   # default 'cloud-user'
              host_port: 2222                    # per-host SLIRP host-side port; default = base + host index
              extra_args: []                     # appended to qemu-system argv
```

**`inventory/group_vars/molecule.yml` — backend selector + defaults**:

```yaml
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

mp_defaults:
  qemu:
    cpus: 2
    memory: 1024
    ssh_user: cloud-user
```

**`roles/qemu/defaults/main.yml` — role-level constants**:

```yaml
mp_qemu_role_defaults:
  cpus: 2
  memory: 1024
  ssh_user: cloud-user

mp_qemu_image_cache_dir: "{{ (lookup('env', 'XDG_CACHE_HOME')
                              | default(lookup('env', 'HOME') ~ '/.cache', true))
                             ~ '/molecule-qemu' }}"
mp_qemu_ssh_key_path: "{{ molecule_ephemeral_directory }}/identity_file"
mp_qemu_wait_timeout: 180
mp_qemu_slirp_port_base: 2222
```

The merge order matches kubevirt's: `mp_qemu_role_defaults <- mp_defaults.qemu <- hostvars[item].mp.qemu`. Only `image` is required and is therefore absent from `mp_qemu_role_defaults`.

### Schema validation

Asserted at the top of `create.yml`, fail-fast: `_mp_specs[host].image` is set and non-empty. Image-cache-dir writability is enforced by the subsequent `file: state=directory mode=0755` step. No driver/network enums to validate (single-driver, single-network design).

## Lifecycle: create

Localhost play; all action delegated through `include_role: tasks_from: create`.

1. **Spec merge** — builds `_mp_specs[host]` from the three layers above.
2. **Validate** — assert `image` is set per host.
3. **Image cache** — `get_url` each unique image URL into `{{ mp_qemu_image_cache_dir }}/{{ image | hash('sha256') }}/disk.qcow2`. Idempotent.
4. **Seed ISO** — render `user-data.j2` + `meta-data.j2`; build NoCloud ISO via `cloud-localds` if present, else `genisoimage -volid cidata -joliet -rock`.
5. **Overlay** — `qemu-img create -f qcow2 -F qcow2 -b <cached base> <ephemeral>/<host>.qcow2`. Optional `qemu-img resize` if `disk_size` set.
6. **KVM detection** — `slurp /dev/kvm` perms once; set `_mp_qemu_kvm_ok` fact. Consumed by `_create_process.yml` to choose `-machine accel=kvm:tcg` vs `accel=tcg`.
7. **Launch** — build qemu-system-x86_64 argv (`-machine accel=...`, `-m`, `-smp`, `-drive file=<overlay>,if=virtio,format=qcow2`, `-cdrom <seed.iso>`, `-netdev user,id=net0,hostfwd=tcp::<port>-:22`, `-device virtio-net-pci,netdev=net0`, `-daemonize`, `-pidfile <ephemeral>/<host>.pid`, `-qmp unix:<ephemeral>/<host>.qmp,server,nowait`, `-display none`, plus `extra_args`). Launch via `ansible.builtin.command` with `creates: <pidfile>`.
8. **Runtime inventory** — for each host, write `ansible_host: 127.0.0.1`, `ansible_port: <host_port or base+index>`, `ansible_user: <ssh_user>`, `ansible_ssh_private_key_file: <key_path>`, `ansible_connection: ssh`, `ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'`. Write to `{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml`; `meta: refresh_inventory`.
9. **`prepare.yml`** runs after create via the molecule lifecycle — `wait_for_connection: timeout={{ mp_qemu_wait_timeout }}`.

### Port assignment

SLIRP host ports are `mp_qemu_slirp_port_base + index_of_host_in_groups.molecule`. Deterministic. Per-host override via `mp.qemu.host_port`. Two molecule scenarios on the same controller share the same port-base range — concurrent scenarios on one controller need a per-scenario `mp_qemu_slirp_port_base` override.

## Lifecycle: destroy

`destroy.yml` is defensive — it tolerates being called against a partially-provisioned scenario.

1. **Spec merge** — same merge as create, with `default({})` on every layer so missing fields don't abort.
2. **Per-host process destroy** — for each `groups['molecule']`: `slurp` the pidfile (`failed_when: false`); if it parsed and `/proc/<pid>/exe` resolves to `qemu-system`, `kill -TERM <pid>`; `wait_for` pidfile absent (30s); if still present, `kill -KILL`; `file: state=absent` on `<host>.qcow2`, `<host>.pid`, `<host>.qmp`, `<host>-seed.iso`.
3. **cidata cleanup** — `file: state=absent` on `<host>-cidata/` directories.

Every `state=absent` step has `failed_when: false` where the missing-resource case is expected.

## Error handling

- Missing `mp.qemu` block → handled by the dispatcher (`playbooks/create.yml`).
- Missing `image` → `_validate.yml`'s assert names the host.
- Image URL unreachable / checksum mismatch → `get_url` surfaces this naturally.
- `qemu-system-x86_64` missing → `_create_process.yml`'s `command` returns 127; surfaces with a clear errno.
- VM hangs at boot → `wait_for_connection` in prepare times out at `mp_qemu_wait_timeout`; destroy still finds the pidfile.
- Stale pidfile (controller killed mid-run) → destroy confirms the pid still maps to `qemu-system` via `/proc/<pid>/exe` slurp before signaling.
- **KVM detection**: `slurp` `/dev/kvm` once. Readable+writable → KVM. Else → TCG, no warning. Permission-locked falls back silently.

**Idempotency**: every file-producing step uses `creates:`; image cache is keyed by URL hash so two scenarios pulling the same image share the cache.

## Testing

**Self-test scenario**: `extensions/molecule/default/` — the same single scenario used by podman and kubevirt. The `instance` host carries three sibling backend blocks (`mp.podman`, `mp.kubevirt`, `mp.qemu`); the active backend is picked by `PROVISIONER` at run time:

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            podman: { image: <fedora-podman-image> }
            kubevirt: { image: <fedora-containerdisk> }
            qemu:
              image: <ubuntu-noble-cloud-image-url>
              image_checksum: <pinned-sha256>
```

Switching backends: `PROVISIONER=podman pytest tests/integration -v -k default`, `PROVISIONER=kubevirt …`, `PROVISIONER=qemu …`. Or directly: `cd extensions/molecule/default && PROVISIONER=qemu molecule test --scenario-name default`.

**`converge.yml`**: gathers facts and debug-prints `ansible_hostname`/`ansible_distribution`. Backend-agnostic.
**`verify.yml`**: `ansible.builtin.ping` each molecule host.

**CI job** (`.github/workflows/tests.yml` → `integration-qemu`):
- Runs on `ubuntu-latest`. Installs `qemu-system-x86_64`, `qemu-utils`, `cloud-image-utils`.
- `/dev/kvm` is **not** available on GitHub-hosted runners — the job exercises the TCG branch.
- `actions/cache@v4` keyed on the Ubuntu cloud image's pinned sha256 maps to `~/.cache/molecule-qemu/`; subsequent runs skip the ~600 MB download.
- Job timeout 30 min. Selector is `-k default` (same as the podman + kubevirt CI jobs), with `PROVISIONER=qemu` picking the backend. `mp_qemu_wait_timeout: 300` override lives in the scenario's `group_vars/molecule.yml`.

**Fast tests**: `tests/integration/qemu/test_qemu_unit.py` covers spec merge, validation, image cache (via `file://` URL), seed ISO build, destroy idempotency on never-created hosts. Plus a `@pytest.mark.slow` E2E that boots the VM under TCG and asserts pidfile + runtime inventory presence.

**Linting**: existing `ansible-lint` and `yamllint` configs pick the role up automatically.

## Versioning

v1.1.0 — additive only.

- New backend, new optional schema keys → minor bump per the v1.0 versioning contract (design.md §"Versioning").
- No breaking changes to podman, kubevirt, or `mp_backend` dispatch.
- No new galaxy dependencies vs. v1.0.

## Out of scope for v1.1

Deferred to future minor versions:

- **libvirt driver.** Future minor; needs upstream `community.libvirt` module fixes and devcontainer-friendly passt sandboxing before it's worth re-attempting.
- **NAT / bridge networking.** SLIRP only in v1.1.
- **Remote libvirt URIs** (`qemu+ssh://...`).
- **QMP action plugin for graceful shutdown.** v1.1 uses `kill -TERM` + `wait_for`.
- **Non-cloud-init images.** Cloud-init is assumed.
- **Windows / non-Linux guests.**
```

- [ ] **Step 2: Prepend SUPERSEDED note to the original plan**

Add the following at the very top of `docs/superpowers/plans/2026-05-21-qemu-backend.md`, before its existing `# qemu backend Implementation Plan` heading:

```markdown
> **⚠️ SUPERSEDED 2026-05-21.** The libvirt driver path was removed from v1.1 after local verification surfaced multiple interacting bugs. The plan below stays as a historical trail of the original two-driver design. The actual v1.1 implementation follows `docs/superpowers/plans/2026-05-21-qemu-simplify-process-only.md` (process-driver only, SLIRP-only).

---

```

- [ ] **Step 3: Delete the now-obsolete libvirt-fixes plan**

```bash
git rm docs/superpowers/plans/2026-05-21-qemu-libvirt-slirp-fixes.md
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-05-21-qemu-backend-design.md \
        docs/superpowers/plans/2026-05-21-qemu-backend.md
git commit -m "docs(qemu): rewrite design spec for process+slirp-only v1.1"
```

---

## Phase 5 — Verify

### Task 16: Lint pass

**Files:** none — verification only.

- [ ] **Step 1: Run ansible-lint on role + scenario**

```bash
ansible-lint roles/qemu/ playbooks/ 2>&1 | tail -20
```

Expected: no errors.

- [ ] **Step 2: Run yamllint**

```bash
yamllint roles/qemu/ tests/integration/qemu/ \
         .github/workflows/tests.yml galaxy.yml \
         changelogs/fragments/qemu-backend.yml
```

Expected: no errors.

- [ ] **Step 3: No commit (verification only).**

---

### Task 17: Run fast tests + process-driver E2E

**Files:** none — verification only.

- [ ] **Step 1: Fast tests**

```bash
python3 -m pytest tests/integration/qemu/test_qemu_unit.py -v -k "not slow" -o addopts="" 2>&1 | tail -15
```

Expected: 5 passed.

- [ ] **Step 2: Process-driver E2E (slow)**

```bash
python3 -m pytest tests/integration/qemu/test_qemu_unit.py::test_process_driver_e2e -v -o addopts="" 2>&1 | tail -10
```

Expected: PASS in any environment with `qemu-system-x86_64` on PATH. If `qemu-system-x86_64` is missing, the test skips — acceptable, CI covers it.

- [ ] **Step 3: Full molecule test under PROVISIONER=qemu (the merge gate)**

This is what makes the user's actual use case work — `molecule test` on the default scenario, with qemu as the backend. The pytest_ansible fixture wraps `molecule test`, so this also covers the `cd extensions/molecule/default && PROVISIONER=qemu molecule test` invocation by exercising the same code path.

```bash
PROVISIONER=qemu python3 -m pytest tests/integration -v -k default -s -o addopts="" 2>&1 | tail -40
```

Expected: dependency → syntax → create → prepare → converge → verify → destroy all green. `PLAY RECAP ... failed=0` on the create/prepare/converge/verify/destroy phases.

If the test times out: bump `mp_qemu_wait_timeout` in `extensions/molecule/default/inventory/group_vars/molecule.yml`. If `qemu-system-x86_64` is missing on PATH, install it (`dnf install qemu-system-x86_64` or `apt install qemu-system-x86`) and retry; this test is the gate, not optional.

- [ ] **Step 4: Podman regression**

Confirm the other backends still work (the dispatcher's allow-list change is the only cross-backend surface and it's already on the branch from earlier):

```bash
PROVISIONER=podman python3 -m pytest tests/integration -v -k default -o addopts="" 2>&1 | tail -10
```

Expected: PASS (if podman is reachable on the host; otherwise skip).

- [ ] **Step 5: No commit (verification only).**

---

### Task 18: Galaxy build dry-run

**Files:** none — verification only.

- [ ] **Step 1: Build the collection artifact**

```bash
ansible-galaxy collection build --output-path /tmp/ . 2>&1 | tail -5
```

Expected: produces `david_igou-molecule_provisioners-1.1.0.tar.gz`. Cleanup:

```bash
rm /tmp/david_igou-molecule_provisioners-1.1.0.tar.gz
```

- [ ] **Step 2: No commit (verification only).**

---

## Self-Review Findings

1. **Spec coverage:**
   - Drop libvirt files → Task 1.
   - Strip libvirt branches from create/destroy/_overlay → Tasks 2, 3, 4.
   - Drop driver-conditional guards → Task 5.
   - Shrink validation + defaults → Task 6.
   - Strip libvirt-related tests + rename fixture → Tasks 7, 8.
   - Simplify scenario + CI → Tasks 9, 10.
   - Drop galaxy dep, rewrite changelog, README, MIGRATION, design spec, supersede old plan → Tasks 11–15.
   - Verification → Tasks 16, 17, 18.

2. **Placeholder scan:** None. Every step contains the literal file content / exact edit / exact command.

3. **Type consistency:**
   - `mp_qemu_role_defaults` drops `driver`, `uri`, `network` everywhere it's referenced (defaults, scenario group_vars, fixtures, MIGRATION table, README).
   - `_mp_specs[item].driver` / `.network.mode` / `.uri` not referenced anywhere after Task 6 — confirmed by `grep -rn '_mp_specs\[item\]\.driver\|\.uri\b\|\.network' roles/ extensions/ tests/integration/qemu/` returning empty (run this after Task 8 to confirm).
   - `process_slirp.yml` → `process.yml` rename: only callers were `test_qemu_unit.py::test_process_driver_e2e` (updated in Task 8) and the plan-doc reference inside Task 11 of the old `2026-05-21-qemu-backend.md` (left as historical text — the file is marked SUPERSEDED in Task 15).
   - `community.libvirt` references removed from `galaxy.yml` (Task 11), CI (Task 10), tests (Tasks 7, 8), role tasks (Tasks 1, 3, 4). One residual mention remains in `docs/superpowers/plans/2026-05-21-qemu-backend.md` — intentional (historical record).

4. **Risk:**
   - The original 2026-05-21 plan and its commit messages still reference libvirt. PR reviewers see two opposite stories in history (`feat(qemu): libvirt-driver destroy with pool/volume/NAT-reservation cleanup` followed by `refactor(qemu): drop libvirt-driver task and template files`). Acceptable — the PR description should call out the scope walkback explicitly.
   - `extra_args` is documented in the new schema but its implementation already exists in `_create_process.yml` (appended to argv). Confirmed.
   - `host_port` is documented in the new schema; the existing role uses `mp_qemu_slirp_port_base + index` and doesn't honor a per-host `host_port` override yet. **This is a real gap** — the design spec advertises the field but the role doesn't read it. Either: (a) add a one-line lookup in `_create_process.yml` (`_ssh_port: "{{ _mp_specs[item].host_port | default(mp_qemu_slirp_port_base | int + (groups['molecule'].index(item) | int)) }}"`), or (b) drop `host_port` from the schema in Task 15. Pick one before Task 17 runs — the test won't catch this. **Resolution: pick (b)** — drop `host_port` from the spec; can come back when actually needed.

5. **Resolution to risk (4) — pre-applied:** The plan's spec rewrite in Task 15 above includes `host_port` in the schema. Before executing Task 15, **remove the `host_port` line from the schema example** in the rewritten spec (lines under "Inventory schema → per-host shape"). Either physically delete the bullet here or apply that single edit during Task 15.
