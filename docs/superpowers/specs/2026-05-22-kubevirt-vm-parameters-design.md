# KubeVirt provisioner: VirtualMachine API parameterization

**Status**: design
**Date**: 2026-05-22
**Backend**: `roles/kubevirt`

## Problem

The KubeVirt provisioner role exposes a narrow per-host schema (`image`, `namespace`, `ssh_user`, `memory`, `ssh_service.type`). Everything else in the rendered `VirtualMachine` is hardcoded in `roles/kubevirt/tasks/_create_vm.yml`:

- CPU: `cores: 2`, no sockets/threads, no model.
- Resources: only `requests.memory`; no `limits`.
- Devices: one disk-bus shape (`virtio`); no way to add extra disks or volumes.
- Networking: one `pod`/`masquerade` interface; no way to add NICs.
- Scheduling: no `nodeSelector`, `tolerations`, or `affinity`.
- Metadata: no user labels/annotations.
- Compute presets: no support for `instancetype`/`preference`.
- Boot source: only `containerDisk`. No `dataVolume` (CDI import from URL or PVC clone) and no direct `persistentVolumeClaim` mount.

Consumers that need any of the above today have to fork the role or work around it. This design widens the schema so the common knobs are first-class, less common needs are reachable through a single escape hatch, and the three meaningful boot-source modes are all supported.

## Goals

1. Make the high-frequency knobs (cpu, memory limits, scheduling, extra disks/volumes/NICs) first-class fields with predictable shape and documentation.
2. Provide a single escape hatch (`vm_overrides`) for anything not surfaced, deep-merged into the rendered `VirtualMachine` with list-append semantics.
3. Support the three boot-source modes that are meaningful for ephemeral test VMs: `container_disk`, `data_volume_url` (CDI import), `data_volume_pvc` (CDI clone), `pvc` (direct mount).
4. Recognize `instancetype`/`preference` so curated `cpu`/`resources` are suppressed when an instancetype is set (KubeVirt rejects conflicting domain fields).
5. Preserve the lifecycle invariants (`spec.running: true`, cloudinit user with our SSH key, NodePort SSH service) by construction of the base spec, while documenting the foot-guns rather than guard-railing them.

## Non-goals

- Other VM-shaped CRDs: `VirtualMachineInstance` (direct VMI), `VirtualMachineInstanceReplicaSet`, `VirtualMachinePool`, `VirtualMachineClone`, `VirtualMachineSnapshot/Restore`. Molecule wants named, addressable, restart-capable instances — `VirtualMachine` is the right kind.
- `cloudInitConfigDrive` (we stick with `cloudInitNoCloud`; they are functionally interchangeable).
- LoadBalancer `ssh_service.type` (still a v1 limitation per the original collection spec).
- Multi-NIC connection: the runtime inventory still uses the pod-network NodePort. If a user adds an `extra_interfaces` Multus NIC, they own any cluster-side routing.
- Integration-testing the non-containerDisk boot modes in CI. CDI is not installed in the kind cluster; adding it is a separate scope-cut.

## Public schema

Per-host under `hostvars[item].mp.kubevirt.*`.

```yaml
mp:
  kubevirt:
    # --- Boot source (REQUIRED, discriminated union) ---
    boot_source:
      type: container_disk          # one of: container_disk | data_volume_url | data_volume_pvc | pvc
      image: quay.io/.../fedora.img # for container_disk

    # alternate: CDI import from URL
    boot_source:
      type: data_volume_url
      url: https://cloud-images.ubuntu.com/.../noble.img
      checksum: "sha256:..."        # optional
      size: 10Gi                    # required
      storage_class: standard       # optional

    # alternate: CDI smart-clone from existing PVC
    boot_source:
      type: data_volume_pvc
      source:
        name: golden-ubuntu
        namespace: images
      size: 10Gi                    # required
      storage_class: standard       # optional

    # alternate: direct mount of existing PVC (no CDI)
    boot_source:
      type: pvc
      name: existing-boot-pvc

    # --- Lifecycle / connection ---
    namespace: molecule             # optional, role default 'molecule'
    ssh_user: cloud-user            # optional, role default 'cloud-user'
    ssh_service:
      type: NodePort                # only NodePort supported in v1

    # --- Curated compute knobs ---
    cpu:
      cores: 4                      # role default 2
      sockets: 1                    # optional
      threads: 1                    # optional
      model: host-passthrough       # optional
    memory: 1Gi                     # → resources.requests.memory, role default '1Gi'
    memory_limit: 2Gi               # → resources.limits.memory (optional)

    # --- Compute presets (alternative to cpu/memory) ---
    instancetype: u1.medium         # string OR { name, kind }
    preference: fedora              # string OR { name, kind }

    # --- Scheduling ---
    node_selector: { kubernetes.io/arch: amd64 }
    tolerations:
      - { key: dedicated, operator: Equal, value: molecule, effect: NoSchedule }
    affinity: { ... }               # passes through verbatim

    # --- Extra disks/volumes/networks (APPENDED to defaults) ---
    extra_disks:
      - name: scratch
        disk: { bus: virtio }
    extra_volumes:
      - name: scratch
        emptyDisk: { capacity: 5Gi }
    extra_interfaces:
      - name: bridge0
        bridge: {}
    extra_networks:
      - name: bridge0
        multus: { networkName: my-net }

    # --- Escape hatch: deep-merged into the whole VirtualMachine object ---
    vm_overrides:
      metadata:
        labels: { team: platform }
        annotations: { foo: bar }
      spec:
        template:
          spec:
            domain:
              machine: { type: q35 }
              firmware:
                bootloader:
                  efi: { secureBoot: false }
            terminationGracePeriodSeconds: 60
```

### Field-resolution order (unchanged)

`mp_kubevirt_role_defaults` ← `mp_defaults.kubevirt` (group_vars) ← `hostvars[item].mp.kubevirt` (per-host).

### Merge semantics

For both `vm_overrides` and the `extra_*` lists: deep-merge, lists append. Implemented with `ansible.builtin.combine(other, recursive=True, list_merge='append')`. No de-duplication — users who name an `extra_disks` entry `cloudinitdisk` will get a duplicate that KubeVirt rejects.

### Validation rules (asserted at create-time)

1. `boot_source` is present and is a dict.
2. `boot_source.type` is one of `container_disk | data_volume_url | data_volume_pvc | pvc`.
3. Per-type required fields:
   - `container_disk`: `image`
   - `data_volume_url`: `url`, `size`
   - `data_volume_pvc`: `source.name`, `source.namespace`, `size`
   - `pvc`: `name`
4. `ssh_service.type` is in `mp_kubevirt_allowed_ssh_service_types` (unchanged).
5. If `instancetype` is set AND `cpu` or `memory_limit` is also set, emit a debug message (not a failure) — the renderer suppresses `domain.cpu` and `domain.resources` so the instancetype wins.

### Breaking changes from v1.0

- Bare `image:` is removed. Consumers must rewrite to `boot_source: {type: container_disk, image: ...}`. Documented in `docs/MIGRATION.md`. CLAUDE.md authorizes breaking changes in alpha.

## Renderer architecture

### File layout

```
roles/kubevirt/tasks/
  create.yml                 — orchestration; merge + validation + loop
  destroy.yml                — unchanged (deletes by name)
  prepare.yml                — unchanged
  _build_vm.yml              — NEW; pure variable assembly per host
  _create_vm.yml             — refactored; calls _build_vm.yml then k8s apply
  _create_vm_dictionary.yml  — unchanged
```

### `_build_vm.yml` (new, per-host include)

Three-layer variable assembly, then escape-hatch merge. Each step is its own `set_fact` so intermediate state is debuggable.

1. **`__mp_base`** — minimum viable VM skeleton:
   - `kind`, `apiVersion`, `metadata.{name,namespace,labels}`
   - `spec.running: true`
   - `spec.template.metadata.labels`
   - `spec.template.spec.volumes` always includes the `cloudinitdisk` volume with our user-data.
   - `spec.template.spec.domain.devices.disks` always includes the `cloudinitdisk` disk.
   - `spec.template.spec.domain.devices.interfaces[0]` is the default `pod`/`masquerade`.
   - `spec.template.spec.networks[0]` is the default `pod`.

2. **`__mp_curated`** — applies first-class fields onto the base:
   - Boot-source dispatch: depending on `boot_source.type`, prepend the boot disk to `disks` and the boot volume to `volumes` (or, for the data_volume_* types, also set `spec.dataVolumeTemplates`).
   - If `instancetype` is unset: set `domain.cpu`, `domain.resources.requests.memory`, optionally `domain.resources.limits.memory`. Default `cpu` is `{cores: 2}` if not supplied (matches today's behavior).
   - If `instancetype` is set: omit `domain.cpu` and `domain.resources` entirely. Set `spec.instancetype` and (if `preference` is set) `spec.preference`.
   - Append `extra_disks` to `domain.devices.disks`, `extra_volumes` to `volumes`, `extra_interfaces` to `domain.devices.interfaces`, `extra_networks` to `networks`.
   - Set `nodeSelector`, `tolerations`, `affinity` if present.

3. **`__mp_kubevirt_vm`** — escape hatch merge:
   ```yaml
   __mp_kubevirt_vm: "{{ __mp_curated | combine(_mp_specs[item].vm_overrides | default({}), recursive=True, list_merge='append') }}"
   ```

### `_create_vm.yml` (refactored)

```yaml
- include_tasks: _build_vm.yml
- k8s:
    state: present
    definition: "{{ __mp_kubevirt_vm }}"
```

### `create.yml` changes

After the existing `ssh_service.type` assertion loop:

- Add a `boot_source` validation loop (rules 1–3 above).
- Add a debug-emit loop for the instancetype+cpu conflict (rule 5).
- Replace the existing per-host `_create_vm.yml` include with the refactored version (no signature change at the loop level).

### Defaults file

`roles/kubevirt/defaults/main.yml` keeps the current `mp_kubevirt_role_defaults` dict (still has `namespace`, `ssh_user`, `memory`, `ssh_service.type`). It does **not** gain entries for the new curated fields:

- `boot_source` is required; no sensible default.
- `cpu` defaults to `{cores: 2}` inside the renderer (keeps the defaults dict shallow).
- The `extra_*` lists default to `[]` inside the renderer.

### Destroy behavior

Unchanged. The VirtualMachine is deleted by name; for the `data_volume_*` boot modes, the `dataVolumeTemplates`-spawned DataVolume is garbage-collected via the ownerReference KubeVirt attaches. The direct `pvc` mode never created a PVC, so it never deletes one.

### Why a separate `_build_vm.yml` instead of one Jinja blob

The rendered VM has at least five conditional branches (boot source × instancetype-or-not × extras present-or-not). A single Jinja template with that many `{% if %}` blocks is unreadable and untestable. Stepwise `set_fact` lets us `debug: var=__mp_curated` between layers and lets the unit tests assert on intermediate state.

## Trust model

Per the brainstorm decision, no guardrails on `vm_overrides`. The renderer's base spec sets `spec.running: true`, the cloudinit user-data, and the SSH service selector — users who deep-merge contradictory values into those paths will get a broken lifecycle. README documents:

- Don't set `spec.running: false` (prepare will hang on `wait_for_connection`).
- Don't replace the `cloudinitdisk` volume; if you must, replicate the `users:` block with `temporary_ssh_public_key` injected.
- Don't change the `metadata.labels.kubevirt.io/domain` label or the SSH Service's selector (they're how the NodePort routes to the pod).

## Testing

### Self-test scenario (integration)

`extensions/molecule/default/inventory/hosts.yml` is updated:

```yaml
kubevirt:
  boot_source:
    type: container_disk
    image: quay.io/kubevirt/fedora-cloud-container-disk-demo:latest
  cpu: { cores: 2 }
  vm_overrides:
    metadata:
      labels:
        test.molecule_provisioners/exercise: vm_overrides
```

This covers: required `boot_source`, first-class `cpu` (proves the renderer applies it), and `vm_overrides` (proves the deep-merge reaches `metadata.labels`). The kubevirt CI job (`-k default`, `PROVISIONER=kubevirt`) exercises the path end-to-end on kind+KubeVirt with `useEmulation`.

Other boot-source variants are NOT integration-tested:
- `data_volume_url` needs CDI installed on the kind cluster (separate scope-cut).
- `data_volume_pvc` and `pvc` need pre-seeded golden PVCs.
- `instancetype` needs a pre-created `VirtualMachineClusterInstancetype`.

### Renderer unit tests (new)

This is the first unit-test surface in the repo. Add `tests/unit/test_kubevirt_render.py` (or similar) that runs `ansible-playbook` against a small stub playbook including `roles/kubevirt/tasks/_build_vm.yml` with fixture hostvars, captures the rendered `__mp_kubevirt_vm`, and asserts on it.

Cases:

| Case | Assertion |
| --- | --- |
| containerDisk + defaults | `__mp_kubevirt_vm` matches the v1.0 rendered shape, `cpu.cores == 2` |
| `data_volume_url` | `spec.dataVolumeTemplates` present with `source.http.url`, boot volume is `dataVolume` |
| `data_volume_pvc` | `spec.dataVolumeTemplates` present with `source.pvc.{name,namespace}` |
| `pvc` | boot volume has `persistentVolumeClaim.claimName`, no `dataVolumeTemplates` |
| `instancetype` set | `domain.cpu` and `domain.resources` are absent; `spec.instancetype` present |
| `extra_disks: [foo]` | final spec has 3 disks (containerdisk OR equivalent, cloudinitdisk, foo) |
| `vm_overrides.metadata.labels` | labels appear on final spec |
| `vm_overrides.spec.template.spec.tolerations: [t]` + `tolerations: [t2]` curated | final spec has both (list-append proof) |

No new infra dependencies (pyyaml is already pulled in by ansible-core).

## Documentation updates

- `roles/kubevirt/README.md` — full schema rewrite; per-`boot_source` example; "Escape hatch and foot-guns" subsection; instancetype suppression note.
- `docs/examples/inventory/hosts.yml` — kubevirt block uses `boot_source:`.
- `docs/MIGRATION.md` — new "v1.0 → kubevirt schema" section: bare `image:` → `boot_source: {type: container_disk, image: ...}`.
- `roles/kubevirt/meta/argument_specs.yml` — extend `create` options docs. (argument_specs cannot validate per-host hostvars, so this is purely for `ansible-doc`.)
- `CLAUDE.md` — refresh the "Public contract" kubevirt schema block.

## Out of scope (deferred)

- CDI installation in the kubevirt CI cluster (only needed if we want to integration-test `data_volume_url`).
- Pre-seeded golden PVC fixtures for `data_volume_pvc` / `pvc` integration tests.
- A `VirtualMachineClusterInstancetype` fixture for instancetype integration tests.
- LoadBalancer `ssh_service.type`.
- VMI-direct provisioning (`VirtualMachineInstance` without a `VirtualMachine` wrapper).

## File-by-file change list

| File | Change |
| --- | --- |
| `roles/kubevirt/defaults/main.yml` | Keep existing dict; no new keys. |
| `roles/kubevirt/tasks/create.yml` | Add `boot_source` validation loop and instancetype-conflict debug loop. |
| `roles/kubevirt/tasks/_build_vm.yml` | NEW — three-layer renderer per host. |
| `roles/kubevirt/tasks/_create_vm.yml` | Refactor: include `_build_vm.yml` then `k8s: definition: "{{ __mp_kubevirt_vm }}"`. |
| `roles/kubevirt/tasks/destroy.yml` | Unchanged. |
| `roles/kubevirt/tasks/prepare.yml` | Unchanged. |
| `roles/kubevirt/tasks/_create_vm_dictionary.yml` | Unchanged. |
| `roles/kubevirt/meta/argument_specs.yml` | Extend `create` docs. |
| `roles/kubevirt/README.md` | Rewrite Inputs section; add boot-source examples; add foot-guns subsection. |
| `extensions/molecule/default/inventory/hosts.yml` | Update kubevirt block: `boot_source` + `cpu` + `vm_overrides`. |
| `docs/examples/inventory/hosts.yml` | Update kubevirt block to use `boot_source`. |
| `docs/MIGRATION.md` | Add v1.0 → kubevirt schema section. |
| `CLAUDE.md` | Refresh Public contract kubevirt schema. |
| `tests/unit/test_kubevirt_render.py` | NEW — renderer unit tests (first unit test in the repo). |
