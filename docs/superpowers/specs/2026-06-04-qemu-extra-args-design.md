# qemu: wire up `mp.qemu.extra_args` passthrough

- **Date:** 2026-06-04
- **Issue:** #50 — qemu: advertised `extra_args` is not wired in
- **Status:** approved

## Problem

The qemu backend advertises a per-host `extra_args` field (documented in
`CHANGELOG.rst` and the qemu backend design spec), but it is consumed nowhere.
`roles/qemu/tasks/_spec_merge.yml` deep-merges arbitrary hostvars into
`_mp_specs[item]`, so `mp.qemu.extra_args` lands in the spec map — but both the
BIOS and UEFI launch tasks in `_create_process.yml` build a **fixed literal
`argv:` list** that ends at `-display none` and never reads it. Setting
`extra_args` in inventory is a silent no-op.

The motivating use case is adding extra NICs to a provisioned VM (e.g. a
MikroTik CHR guest that needs `ether2`, `ether3`, … to test RouterOS network
roles). The role launches each VM with a single SLIRP NIC (`net0`, the SSH
hostfwd) and there is no supported way to add more. Hot-plugging over the QMP
socket works but is lost on every relaunch, so it cannot live in a scenario.

## Scope

In scope: wire the existing `extra_args` field through to the qemu argv,
validate it, test it, document it. **Raw `extra_args` only** — the escape
hatch. Out of scope (deferred to a future issue): a structured `nics:` helper
that expands to `-netdev`/`-device` pairs, and tap/socket L2 networking between
VMs. `extra_args` already covers every acceptance criterion in the issue.

## Design

### 1. Passthrough — `roles/qemu/tasks/_create_process.yml`

Both launch tasks ("Launch qemu-system (bios)" and "Launch qemu-system
(uefi)") currently inline a literal `argv:` list. In each task, move that list
into a `vars:` entry `_base_argv` and concatenate the per-host extra args:

```yaml
argv: "{{ _base_argv + (_mp_specs[item].extra_args | default([])) }}"
```

`extra_args` already arrives in `_mp_specs[item]` via the existing deep-merge —
no `_spec_merge.yml` change is needed, only consumption. Both firmware paths
get identical treatment so the behavior is the same regardless of `firmware:`.
`| default([])` handles hosts that never set the field.

### 2. Validation — `roles/qemu/tasks/_validate.yml`

Add an assert looping `groups['molecule']`: when `extra_args` is defined it
must be a sequence-and-not-a-string, and every element must be a string. The
`fail_msg` names the host, includes `type_debug`, and instructs the user to
write a quoted YAML list, e.g. `['-netdev','user,id=net1', ...]`. This catches
the documented footgun (a bare string) and nested structures the `command`
module's `argv` would choke on.

### 3. Test (write first — TDD) — `extensions/molecule/default/`

There is a real `integration-qemu` CI job that runs the default scenario under
TCG, so the test lives in the scenario and is exercised automatically.

- `inventory/hosts.yml`: add `extra_args` to the qemu host declaring a second
  SLIRP NIC: `[-netdev, "user,id=net1", -device, "virtio-net-pci,netdev=net1"]`.
- `verify.yml`: add a qemu-gated play (`gather_facts: true`, gated on
  `PROVISIONER == 'qemu'`) asserting the guest enumerates ≥2 non-loopback
  interfaces. The kernel enumerates the virtio device even if cloud-init never
  configures it, so this proves the NIC reached the guest.

The assertion fails on current code (extra_args dropped → only `net0`) and
passes after step 1. Persistence across destroy/create is structural — the role
always relaunches from inventory, never hot-plug — so one CI cycle suffices;
the plan notes this rather than running two cycles.

### 4. Docs & housekeeping

- `roles/qemu/README.md`: add an `extra_args` row to the inputs schema with the
  extra-NIC example as the canonical use case.
- Correct the stale "Confirmed" note at
  `docs/superpowers/plans/2026-05-21-qemu-simplify-process-only.md:1382`.
- Add a `changelogs/fragments/` entry (CI's changelog check expects one).

No `argument_specs.yml` exists for qemu (it validates via `_validate.yml`
asserts), so there is nothing to update there.

## Acceptance criteria (from issue #50)

- [ ] `mp.qemu.extra_args` is appended to the launched qemu argv (BIOS + UEFI).
- [ ] Invalid `extra_args` (non-list) fails validation with a clear message.
- [ ] A scenario can declare ≥1 extra NIC and the guest sees the corresponding
  interface after create (persistent by construction across destroy/create).
- [ ] `destroy` is unaffected.
- [ ] Stale "Confirmed" note at the plan doc line 1382 corrected.
