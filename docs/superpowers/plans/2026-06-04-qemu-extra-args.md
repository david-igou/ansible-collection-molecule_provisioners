# qemu `extra_args` Passthrough Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-advertised `mp.qemu.extra_args` per-host field through to the launched `qemu-system` argv (BIOS + UEFI), validate it, test it, and document it — so consumers can add extra NICs (or any qemu CLI flag) to a provisioned VM.

**Architecture:** Both launch tasks in `roles/qemu/tasks/_create_process.yml` currently inline a fixed literal `argv:` list. Move each list into a `vars._base_argv` entry and append `_mp_specs[item].extra_args | default([])`. The field already arrives in `_mp_specs[item]` via the existing deep-merge in `_spec_merge.yml`, so only *consumption* and *validation* are added. A new qemu-gated assertion in the self-test scenario's `verify.yml` proves the extra NIC reaches the guest; the existing `integration-qemu` CI job exercises it under TCG.

**Tech Stack:** Ansible (`ansible.builtin.command` with `argv`), Molecule (ansible-native default scenario), qemu/TCG, pytest-ansible.

**Spec:** `docs/superpowers/specs/2026-06-04-qemu-extra-args-design.md`

**Branch:** `feat/50-qemu-extra-args` (already checked out; the spec is committed here).

---

## File Structure

- **Modify** `extensions/molecule/default/inventory/hosts.yml` — add `extra_args` (a second SLIRP NIC) to the qemu host. *(test fixture)*
- **Modify** `extensions/molecule/default/verify.yml` — add a qemu-gated play asserting the guest enumerates the extra NIC. *(test)*
- **Modify** `roles/qemu/tasks/_create_process.yml` — append `extra_args` to argv in both the BIOS and UEFI launch tasks. *(implementation)*
- **Modify** `roles/qemu/tasks/_validate.yml` — assert `extra_args` is a list of strings when set. *(implementation)*
- **Modify** `roles/qemu/README.md` — document `extra_args` with the extra-NIC example. *(docs)*
- **Create** `changelogs/fragments/50-qemu-extra-args.yml` — changelog entry. *(housekeeping)*
- **Modify** `docs/superpowers/plans/2026-05-21-qemu-simplify-process-only.md:1382` — correct the stale "Confirmed" note. *(housekeeping)*

---

## Task 1: Add the failing NIC test (fixture + assertion)

**Files:**
- Modify: `extensions/molecule/default/inventory/hosts.yml` (qemu host block, around lines 56-62)
- Modify: `extensions/molecule/default/verify.yml` (append a new play at end of file)

- [ ] **Step 1: Add `extra_args` to the qemu host in the inventory**

In `extensions/molecule/default/inventory/hosts.yml`, the qemu block currently reads:

```yaml
            qemu:
              # Immutable snapshot URL (NOT noble/current/, which is a moving daily build).
              # When bumping, update both the URL date segment and the matching checksum
              # from the same SHA256SUMS file at that release path.
              image: >-
                https://cloud-images.ubuntu.com/releases/noble/release-20260518/ubuntu-24.04-server-cloudimg-amd64.img
              image_checksum: "sha256:53fdde898feed8b027d94baa9cfe8229867f330a1d9c49dc7d84465ee7f229f7"
```

Add an `extra_args` key declaring a second SLIRP NIC (`net1`) so it appears in the guest as a second interface:

```yaml
            qemu:
              # Immutable snapshot URL (NOT noble/current/, which is a moving daily build).
              # When bumping, update both the URL date segment and the matching checksum
              # from the same SHA256SUMS file at that release path.
              image: >-
                https://cloud-images.ubuntu.com/releases/noble/release-20260518/ubuntu-24.04-server-cloudimg-amd64.img
              image_checksum: "sha256:53fdde898feed8b027d94baa9cfe8229867f330a1d9c49dc7d84465ee7f229f7"
              # Exercises #50: a second SLIRP NIC appended to the qemu argv.
              # The guest kernel enumerates virtio-net even though cloud-init
              # never configures net1, so it shows up in ansible_interfaces.
              extra_args:
                - -netdev
                - "user,id=net1"
                - -device
                - "virtio-net-pci,netdev=net1"
```

- [ ] **Step 2: Append the qemu-gated verification play to `verify.yml`**

Add this as a new play at the **end** of `extensions/molecule/default/verify.yml` (after the existing podman-localhost play):

```yaml
- name: Verify — qemu guest received the extra_args NIC
  hosts: molecule
  gather_facts: true
  vars:
    # Source the backend from PROVISIONER (matches inventory/group_vars/molecule.yml).
    _mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"
  tasks:
    - name: Skip non-qemu backends
      ansible.builtin.meta: end_play
      when: _mp_backend != 'qemu'

    # Regression for #50: mp.qemu.extra_args was advertised but dropped, so the
    # VM always launched with only net0. The inventory declares a second NIC
    # (net1) via extra_args; the guest kernel enumerates it regardless of
    # cloud-init, so a passthrough that works yields >=2 non-loopback interfaces.
    - name: Assert the guest enumerates the extra_args NIC
      ansible.builtin.assert:
        that:
          - (ansible_interfaces | reject('equalto', 'lo') | list | length) >= 2
        fail_msg: >-
          Expected >=2 non-loopback interfaces in the qemu guest (net0 + the
          extra_args net1); got
          {{ ansible_interfaces | reject('equalto', 'lo') | list }}.
          mp.qemu.extra_args was likely dropped instead of appended to the
          qemu argv.
        success_msg: >-
          Guest sees
          {{ ansible_interfaces | reject('equalto', 'lo') | list | length }}
          non-loopback interfaces — the extra_args NIC reached the VM.
```

- [ ] **Step 3: Run the qemu scenario to confirm the test FAILS (RED)**

TCG boots are slow; use molecule subcommands for a faster loop than a full `molecule test`:

```bash
cd extensions/molecule/default
PROVISIONER=qemu molecule create
PROVISIONER=qemu molecule prepare
PROVISIONER=qemu molecule verify
```

Expected: `molecule verify` FAILS at "Assert the guest enumerates the extra_args NIC" — the guest has only `lo` + one NIC because `extra_args` is dropped by the current role. (`molecule create`/`prepare` succeed.)

Leave the VM running for Task 2's GREEN check (do not `molecule destroy` yet).

- [ ] **Step 4: Commit the failing test**

```bash
cd /workspace/ansible-collection-molecule_provisioners
git add extensions/molecule/default/inventory/hosts.yml extensions/molecule/default/verify.yml
git commit -m "test(qemu): assert extra_args NIC reaches the guest (#50)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Implement the `extra_args` passthrough

**Files:**
- Modify: `roles/qemu/tasks/_create_process.yml` (BIOS launch task lines 41-84; UEFI launch task lines 89-131)

- [ ] **Step 1: Append `extra_args` in the BIOS launch task**

Replace the entire "Launch qemu-system (bios)" task with the version below. The change: the literal list moves into `vars._base_argv`, and `argv:` becomes a concatenation. Everything else (the `vars` already present, `when`, `loop`) is unchanged.

```yaml
- name: "Launch qemu-system (bios) for {{ item }}"
  vars:
    _ssh_port: "{{ mp_qemu_slirp_port_base | int + (groups['molecule'].index(item) | int) }}"
    _accel: "{{ 'kvm:tcg' if _mp_qemu_kvm_ok | default(false) else 'tcg' }}"
    # Default 'host' under KVM (full feature passthrough), 'Nehalem' under
    # TCG. Without an explicit -cpu, qemu falls through to a model that
    # strips x86-64-v2 features and RHEL/Rocky/CentOS 9+ panic at first
    # userspace process because glibc requires v2. Nehalem is the oldest
    # named CPU model that provides v2 features (SSE4.2, popcnt) and has
    # shipped in qemu for years — `qemu64-v2` is qemu 8.0+ only.
    _cpu_model: >-
      {{ _mp_specs[item].cpu_model
         | default('host' if _mp_qemu_kvm_ok | default(false) else 'Nehalem', true) }}
    _base_argv:
      - qemu-system-x86_64
      - -machine
      - "accel={{ _accel }}"
      - -cpu
      - "{{ _cpu_model }}"
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
  ansible.builtin.command:
    # Append per-host extra_args (raw qemu CLI flags, e.g. extra NICs). The
    # field arrives in _mp_specs via _spec_merge.yml's deep-merge; _validate.yml
    # guarantees it is a list of strings when set. See issue #50.
    argv: "{{ _base_argv + (_mp_specs[item].extra_args | default([])) }}"
    creates: "{{ molecule_ephemeral_directory }}/{{ item }}.pid"
  when: _mp_specs[item].firmware | default('bios') != 'uefi'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 2: Append `extra_args` in the UEFI launch task**

Replace the entire "Launch qemu-system (uefi)" task with the version below. Same change pattern; the UEFI argv keeps its leading pflash drives and `qemu64-v2` TCG default.

```yaml
- name: "Launch qemu-system (uefi) for {{ item }}"
  vars:
    _ssh_port: "{{ mp_qemu_slirp_port_base | int + (groups['molecule'].index(item) | int) }}"
    _accel: "{{ 'kvm:tcg' if _mp_qemu_kvm_ok | default(false) else 'tcg' }}"
    _ovmf_vars_path: "{{ molecule_ephemeral_directory }}/{{ item }}-ovmf-vars.fd"
    _cpu_model: >-
      {{ _mp_specs[item].cpu_model
         | default('host' if _mp_qemu_kvm_ok | default(false) else 'qemu64-v2', true) }}
    _base_argv:
      - qemu-system-x86_64
      - -drive
      - "if=pflash,format=raw,readonly=on,file={{ mp_qemu_ovmf_code }}"
      - -drive
      - "if=pflash,format=raw,file={{ _ovmf_vars_path }}"
      - -machine
      - "accel={{ _accel }}"
      - -cpu
      - "{{ _cpu_model }}"
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
  ansible.builtin.command:
    # Append per-host extra_args (raw qemu CLI flags, e.g. extra NICs). See #50.
    argv: "{{ _base_argv + (_mp_specs[item].extra_args | default([])) }}"
    creates: "{{ molecule_ephemeral_directory }}/{{ item }}.pid"
  when: _mp_specs[item].firmware | default('bios') == 'uefi'
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 3: Lint the role**

```bash
cd /workspace/ansible-collection-molecule_provisioners
ansible-lint roles/qemu/
```

Expected: no new errors.

- [ ] **Step 4: Run the qemu scenario to confirm the test PASSES (GREEN)**

The `creates:` guard means re-running against the VM left up from Task 1 will *not* relaunch qemu with the new argv. Recreate from scratch:

```bash
cd extensions/molecule/default
PROVISIONER=qemu molecule destroy
PROVISIONER=qemu molecule create
PROVISIONER=qemu molecule prepare
PROVISIONER=qemu molecule verify
```

Expected: `molecule verify` PASSES — "Guest sees 2 non-loopback interfaces — the extra_args NIC reached the VM."

- [ ] **Step 5: Commit**

```bash
cd /workspace/ansible-collection-molecule_provisioners
git add roles/qemu/tasks/_create_process.yml
git commit -m "feat(qemu): append mp.qemu.extra_args to the launched qemu argv (#50)

Both BIOS and UEFI launch paths now concatenate the per-host extra_args
list onto the base argv. Previously the field was deep-merged into the
spec map but never read.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Validate `extra_args` is a list of strings

**Files:**
- Modify: `roles/qemu/tasks/_validate.yml` (append a new assert task)

- [ ] **Step 1: Add the validation assert**

Append this task to `roles/qemu/tasks/_validate.yml` (after the existing "Validate image is set per host" task):

```yaml
- name: Validate extra_args is a list of strings when set
  ansible.builtin.assert:
    that:
      - >-
        _mp_specs[item].extra_args is not defined
        or (_mp_specs[item].extra_args is sequence
            and _mp_specs[item].extra_args is not string
            and (_mp_specs[item].extra_args | select('string') | list | length)
                == (_mp_specs[item].extra_args | length))
    fail_msg: >-
      Host '{{ item }}' has an invalid mp.qemu.extra_args
      ({{ _mp_specs[item].extra_args | type_debug }}). It must be a YAML list of
      qemu CLI argument strings — quote each element, e.g.
      ['-netdev', 'user,id=net1', '-device', 'virtio-net-pci,netdev=net1'].
  loop: "{{ groups['molecule'] }}"
  loop_control:
    label: "{{ item }}"
```

The condition passes when `extra_args` is unset; otherwise it must be a
sequence that is not a string (rejects a bare string), and every element must
be a string (the `select('string')` count equals the total length — rejects a
list containing ints, dicts, or nested lists).

- [ ] **Step 2: Confirm valid `extra_args` still passes (the scenario's value)**

```bash
cd extensions/molecule/default
PROVISIONER=qemu molecule create
```

Expected: the "Validate extra_args is a list of strings when set" task shows `ok` for the qemu host (its `extra_args` is a valid list), and create proceeds. (If the VM from Task 2 is still up, run `molecule destroy` first.)

- [ ] **Step 3: Confirm an invalid `extra_args` FAILS (manual RED — revert after)**

Temporarily set a bare string in the inventory to prove the assert fires. In `extensions/molecule/default/inventory/hosts.yml`, change the qemu host's `extra_args` to a scalar:

```yaml
              extra_args: "-netdev user,id=net1"
```

Then run validation only (destroy first so create actually re-runs the merge/validate):

```bash
cd extensions/molecule/default
PROVISIONER=qemu molecule destroy
PROVISIONER=qemu molecule create
```

Expected: create FAILS fast at "Validate extra_args is a list of strings when set" with the `fail_msg` naming the host and `type_debug` reporting `str`.

**Revert the inventory** back to the valid list from Task 1 Step 1:

```yaml
              extra_args:
                - -netdev
                - "user,id=net1"
                - -device
                - "virtio-net-pci,netdev=net1"
```

Re-run `PROVISIONER=qemu molecule destroy && PROVISIONER=qemu molecule create` and confirm validation passes again.

- [ ] **Step 4: Lint and commit**

```bash
cd /workspace/ansible-collection-molecule_provisioners
ansible-lint roles/qemu/
git add roles/qemu/tasks/_validate.yml
git commit -m "feat(qemu): validate mp.qemu.extra_args is a list of strings (#50)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Docs, changelog, and stale-note correction

**Files:**
- Modify: `roles/qemu/README.md` (Inputs schema, after the `disk_size` line ~30)
- Create: `changelogs/fragments/50-qemu-extra-args.yml`
- Modify: `docs/superpowers/plans/2026-05-21-qemu-simplify-process-only.md:1382`

- [ ] **Step 1: Document `extra_args` in the qemu README**

In `roles/qemu/README.md`, the Inputs schema block ends with the `disk_size` line:

```yaml
              disk_size: ""                                                  # optional; resizes the overlay and grows root on first boot via cloud-init growpart
```

Add an `extra_args` line directly below it (inside the same `qemu:` mapping):

```yaml
              extra_args: []                                                 # optional; raw qemu-system CLI args appended to argv (BIOS and UEFI). Must be a flat list of strings — quote elements containing '='. Canonical use is extra NICs.
```

Then add this short prose block immediately after the schema code fence (before the "Shared defaults can be hoisted…" paragraph):

```markdown
### Adding extra NICs with `extra_args`

The role launches each VM with a single SLIRP NIC (`net0`, the SSH hostfwd).
To give a guest more interfaces, append `-netdev`/`-device` pairs via
`extra_args`:

```yaml
mp:
  qemu:
    image: ...
    extra_args:
      - -netdev
      - "user,id=net1"
      - -device
      - "virtio-net-pci,netdev=net1"
```

This adds a second NIC the guest kernel enumerates on boot. `extra_args` is a
raw escape hatch — its elements are passed to `qemu-system-x86_64` verbatim, so
any qemu flag works, not just networking.
```

- [ ] **Step 2: Create the changelog fragment**

First confirm the repo URL for the issue link:

```bash
cd /workspace/ansible-collection-molecule_provisioners
git remote get-url origin
```

Create `changelogs/fragments/50-qemu-extra-args.yml` (substitute the org/repo from the remote URL if it differs from `david-igou/ansible-collection-molecule_provisioners`):

```yaml
---
bugfixes:
  - qemu - ``mp.qemu.extra_args`` is now appended to the launched ``qemu-system`` argv on both the BIOS and UEFI paths. It was previously advertised in the per-host schema but silently dropped, leaving no supported way to add extra NICs or other qemu CLI flags to a provisioned VM (https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/50).
minor_changes:
  - qemu - ``mp.qemu.extra_args`` is now validated as a list of strings, failing fast with a clear message on a bare string or non-string elements (https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/50).
```

- [ ] **Step 3: Correct the stale "Confirmed" note**

In `docs/superpowers/plans/2026-05-21-qemu-simplify-process-only.md`, line 1382 currently reads:

```
   - `extra_args` is documented in the new schema but its implementation already exists in `_create_process.yml` (appended to argv). Confirmed.
```

Replace it with:

```
   - `extra_args` was documented in the new schema but its implementation did **not** exist — the launch tasks built a fixed `argv` list and never appended it; the earlier "Confirmed" was wrong. Wired up in #50 (2026-06-04): appended to argv in both the BIOS and UEFI launch tasks, with list-of-strings validation in `_validate.yml`.
```

- [ ] **Step 4: Validate the changelog fragment**

```bash
cd /workspace/ansible-collection-molecule_provisioners
antsibull-changelog lint
```

Expected: no errors for `changelogs/fragments/50-qemu-extra-args.yml`.

- [ ] **Step 5: Commit**

```bash
cd /workspace/ansible-collection-molecule_provisioners
git add roles/qemu/README.md changelogs/fragments/50-qemu-extra-args.yml docs/superpowers/plans/2026-05-21-qemu-simplify-process-only.md
git commit -m "docs(qemu): document extra_args, add changelog, fix stale note (#50)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Full-scenario green + cleanup

**Files:** none (verification only)

- [ ] **Step 1: Run the full qemu lifecycle end-to-end**

Mirror what the `integration-qemu` CI job runs:

```bash
cd /workspace/ansible-collection-molecule_provisioners
PROVISIONER=qemu pytest tests/integration -v -k default -s -o addopts=""
```

Expected: PASS — full `molecule test` (create → converge → verify → destroy) succeeds, including the new "Assert the guest enumerates the extra_args NIC" task. This also confirms `destroy` is unaffected (it never touched NIC config).

- [ ] **Step 2: Confirm the podman backend is unaffected (regression guard)**

The new `verify.yml` play is qemu-gated; confirm it no-ops elsewhere:

```bash
PROVISIONER=podman pytest tests/integration -v -k default
```

Expected: PASS — the new play hits "Skip non-qemu backends" (`end_play`) and the existing podman assertions still pass.

- [ ] **Step 3: Final lint sweep**

```bash
cd /workspace/ansible-collection-molecule_provisioners
ansible-lint && yamllint .
```

Expected: clean.

---

## Acceptance Criteria Trace (issue #50)

- `mp.qemu.extra_args` appended to qemu argv (BIOS + UEFI) → **Task 2** (both tasks) + **Task 5 Step 1**.
- Invalid `extra_args` (non-list) fails validation with a clear message → **Task 3**.
- A scenario declares ≥1 extra NIC and the guest sees it after create; persistent across destroy/create → **Task 1** (fixture + assertion) + **Task 5 Step 1**. Persistence is structural: the role relaunches from inventory every create (never hot-plug), so each create re-applies `extra_args`.
- `destroy` unaffected → **Task 5 Step 1** (full lifecycle ends in destroy).
- Stale "Confirmed" note at plan line 1382 corrected → **Task 4 Step 3**.
