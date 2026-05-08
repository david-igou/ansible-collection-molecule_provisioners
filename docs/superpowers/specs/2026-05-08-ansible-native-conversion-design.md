# Ansible-native conversion design

**Date:** 2026-05-08
**Status:** Approved (supersedes `2026-05-08-molecule-provisioners-design.md` for the v1.0 release shape)

## Goal

Ship `david_igou.molecule_provisioners` v1.0 with an ansible-native scenario layout. Consumers describe test instances in `inventory/hosts.yml` (not `platforms:`); `molecule.yml` uses the `ansible:` block (not `driver:` + `provisioner:`); the dispatcher → role → `tasks_from` pattern is unchanged.

## Why

The molecule project's own documentation classifies `platforms:` and `driver:` as **pre ansible-native** constructs (`ansible/molecule:docs/pre-ansible-native.md`) and directs users toward an ansible-native configuration shape (`docs/ansible-native.md`) that uses standard Ansible inventory plus collection-shipped lifecycle playbooks. This collection's *thesis* (replace `molecule-plugins` with create/destroy/prepare playbooks shipped in a collection) already aligns with that direction. Its *config surface* did not. v1.0 has not shipped to Galaxy and no tag exists, so the v1.0 release shape can adopt ansible-native directly without a deprecation cycle.

## Locked-in decisions

1. **v1.0 ships ansible-native from day one.** No `platforms:` ever appears in the public contract. No backward-compat shim for any earlier shape — there is no released earlier shape.
2. **Per-host config shape is a nested `mp.<backend>.<field>` hostvar dict on each host.** Preserves the v1 design's "same scenario works under either backend by switching `$PROVISIONER`" property. Pure flat hostvars would force two scenarios per consumer to support both backends; the nested dict keeps it to one.
3. **Backend selection is consumer-driven via `mp_backend` group var.** The collection reads `hostvars[groups['molecule'][0]].mp_backend` from inventory. The `$PROVISIONER` env var is not part of the contract — it is one way for consumers to populate `mp_backend`, and the example boilerplate uses it, but the contract is "set `mp_backend` to `podman` or `kubevirt`."
4. **3-level layering for per-host fields:** role defaults → `mp_defaults.<backend>` group var → `hostvars[item].mp.<backend>` hostvar. Consumers DRY shared bits via `mp_defaults`; `image` is the only field that must be per-host.

## Scope

**In:**
- podman + kubevirt backends only.
- KubeVirt service type: NodePort only.
- Single self-test scenario exercising both backends via CI matrix on `PROVISIONER`.

**Out (carried over from the v1 design spec):**
- docker, qemu/libvirt, AWS/Azure/GCP, other cloud backends.
- LoadBalancer / ClusterIP+port-forward kubevirt service types.
- Windows / macOS guests.
- Mixing backends within a single scenario run — i.e., bringing up some hosts via podman *and* others via kubevirt at the same time. (Defining both `mp.podman` and `mp.kubevirt` blocks on the same host is the design's intended dual-backend shape; only one backend is active per run, selected by `mp_backend`.)
- Molecule's `shared_state` / shared default-scenario pattern.

## Public contract

The consumer's scenario directory has eight files. Five are fixed boilerplate; three describe their actual test fleet.

### `extensions/molecule/<scenario>/molecule.yml` (boilerplate)

```yaml
---
ansible:
  executor:
    args:
      ansible_playbook:
        - --inventory=inventory/
  env:
    ANSIBLE_INVENTORY: "inventory/:${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/"
  playbooks:
    create: create.yml
    destroy: destroy.yml
    prepare: prepare.yml
    converge: converge.yml
    verify: verify.yml

scenario:
  test_sequence: [create, prepare, converge, verify, destroy]

verifier:
  name: ansible
```

The `--inventory=inventory/` arg loads the consumer's static inventory. `ANSIBLE_INVENTORY` chains in `${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/` so the role's runtime-discovered connection details (kubevirt NodePort + node IP, podman connection plugin) layer in for `prepare`/`converge`/`verify`.

> **Implementation note:** This relies on molecule expanding `${MOLECULE_EPHEMERAL_DIRECTORY}` in `ansible.env` values when constructing the playbook environment. The implementation plan must verify this against the molecule version pinned in `test-requirements.txt`. If molecule does not expand env vars in this position, the fallback is to write the runtime inventory directly into the consumer's `inventory/` dir during create and remove it during destroy — slightly less clean (mutates the consumer's tree at runtime) but avoids the env-var-expansion dependency.

### `create.yml` / `destroy.yml` / `prepare.yml` (boilerplate one-liners)

```yaml
- name: Provision molecule instances
  import_playbook: david_igou.molecule_provisioners.create
```

`destroy.yml` and `prepare.yml` mirror this with the matching FQCN. Names are required (ansible-lint 26.4+ `name[play]`).

### `inventory/hosts.yml` (per-scenario)

```yaml
all:
  children:
    molecule:
      hosts:
        fedora:
          mp:
            podman:
              image: docker.io/geerlingguy/docker-fedora41-ansible:latest
            kubevirt:
              image: quay.io/kubevirt/fedora-cloud-container-disk-demo:latest
        ubuntu:
          mp:
            podman:
              image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
            kubevirt:
              image: quay.io/containerdisks/ubuntu:24.04
              ssh_user: ubuntu
```

### `inventory/group_vars/molecule.yml` (per-scenario)

```yaml
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

mp_defaults:
  podman:
    command: /sbin/init
    privileged: true
  kubevirt:
    namespace: molecule
    memory: 1Gi
    disk_size: 5Gi
    ssh_user: cloud-user
```

### Consumer's `converge.yml` / `verify.yml`

The consumer's own play, targeting `hosts: molecule`. Outside the collection's contract.

## Schema (per-host `mp.<backend>.*` fields)

The 3-level layering means only `image` is required per-host. Anything else can resolve to a role default or a `mp_defaults` value.

### `mp.podman.*`

| Field | Required | Role default | Notes |
| --- | --- | --- | --- |
| `image` | yes | — | OCI reference |
| `command` | no | `/sbin/init` | |
| `privileged` | no | `false` | |
| `volumes` | no | `[]` | |
| `capabilities` | no | `[]` | |
| `podman_network` | no | omit | |
| `env` | no | `{}` | |
| `tmpfs` | no | omit | |
| `exposed_ports` | no | omit | |
| `published_ports` | no | omit | |

### `mp.kubevirt.*`

| Field | Required | Role default | Notes |
| --- | --- | --- | --- |
| `image` | yes | — | containerDisk reference |
| `namespace` | no | `molecule` | |
| `ssh_user` | no | `cloud-user` | Override for non-Fedora images |
| `memory` | no | `1Gi` | |
| `disk_size` | no | `5Gi` | |
| `ssh_service.type` | no | `NodePort` | NodePort is the only supported value in v1 |

This is more permissive than the v1 design spec, where `namespace`, `ssh_user`, `memory`, and `disk_size` were required per platform. The 3-level merge satisfies all of them automatically.

## Internals

### Dispatcher (`playbooks/{create,destroy,prepare}.yml`)

Each dispatcher runs on `hosts: localhost`, validates the inventory shape, picks the backend, and `include_role`s the matching role with `tasks_from: <phase>`. `playbooks/create.yml`:

```yaml
- name: Molecule provisioner — create
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Assert molecule group exists
      ansible.builtin.assert:
        that:
          - "'molecule' in groups"
          - groups['molecule'] | length > 0
        fail_msg: "Inventory must define a 'molecule' group with at least one host."

    - name: Determine backend from molecule group
      ansible.builtin.set_fact:
        _mp_backend: "{{ hostvars[groups['molecule'][0]].mp_backend | default(none) }}"

    - name: Validate backend
      ansible.builtin.assert:
        that: _mp_backend in mp_supported_backends
        fail_msg: >-
          mp_backend must be one of {{ mp_supported_backends | join(', ') }}
          (got '{{ _mp_backend or '(unset)' }}'). Set it in
          inventory/group_vars/molecule.yml.

    - name: Validate every host has the active backend block
      ansible.builtin.assert:
        that: hostvars[item].mp[_mp_backend] is defined
        fail_msg: "Host '{{ item }}' is missing mp.{{ _mp_backend }} in inventory."
      loop: "{{ groups['molecule'] }}"

    - name: Run provisioner create
      ansible.builtin.include_role:
        name: "david_igou.molecule_provisioners.{{ _mp_backend }}"
        tasks_from: create
```

`mp_supported_backends` is defined in `playbooks/group_vars/all.yml` (or as a play-level var) as `[podman, kubevirt]`. `destroy.yml` and `prepare.yml` mirror this dispatcher with their respective `tasks_from`.

### Role-level merge (per-phase, before any provisioning loops)

Each role's `tasks/<phase>.yml` begins by computing per-host merged specs, layering `mp_defaults.<backend>` under `hostvars[item].mp.<backend>`. The role-private result is keyed by host name:

```yaml
- name: Initialize spec map
  ansible.builtin.set_fact:
    _mp_specs: {}

- name: Merge per-host specs with mp_defaults and role defaults
  ansible.builtin.set_fact:
    _mp_specs: >-
      {{ _mp_specs | combine({
           item: mp_podman_role_defaults
                 | combine(mp_defaults['podman'] | default({}))
                 | combine(hostvars[item].mp['podman'])
         }) }}
  loop: "{{ groups['molecule'] }}"
```

`mp_podman_role_defaults` is a single dict in `roles/podman/defaults/main.yml` aggregating every optional field's default value (the kubevirt role uses `mp_kubevirt_role_defaults` similarly). One dict-valued default keeps the merge expression a clean three-way combine. After this step, every later task in the role reads `_mp_specs[item].image`, `_mp_specs[item].namespace`, etc. The merge step makes the layering invisible to the rest of the role.

### Loop replacements (concrete diff against v1)

| v1 expression | v2 expression |
| --- | --- |
| `loop: "{{ molecule_yml.platforms }}"` | `loop: "{{ groups['molecule'] }}"` |
| `item.name` (inside body) | `item` (item is the host name string) |
| `item.podman.image` | `_mp_specs[item].image` |
| `vm.kubevirt.namespace \| default(mp_kubevirt_namespace)` | `_mp_specs[item].namespace` (default already merged in) |
| Loop var `vm` (kubevirt role) | Loop var `item` (uniformity) |

### Inventory generation: scope shrinks

In v1 the role had to *create* the molecule group from scratch (writing `inventory/molecule_inventory.yml` with `all.children.molecule.hosts`) because `platforms:` entries are not Ansible hosts. In v2 the consumer's static inventory already declares the molecule group. The role only needs to *augment* hosts with runtime-discovered connection details:

- **podman role** writes a single line per host into `$MOLECULE_EPHEMERAL_DIRECTORY/inventory/molecule_runtime.yml`: `ansible_connection: containers.podman.podman`. (In v1, this was set via the molecule.yml `driver.options.ansible_connection_options` block, which is gone.)
- **kubevirt role** writes `ansible_host`, `ansible_port`, `ansible_user`, `ansible_ssh_private_key_file`, `ansible_connection: ssh` per host. The NodePort and node IP are discovered the same way as in v1.

After writing the file, the role calls `meta: refresh_inventory`. The `ANSIBLE_INVENTORY` chain configured in `molecule.yml` makes subsequent phases pick the runtime file up.

### Validation

`roles/<backend>/meta/argument_specs.yml` validates role-invocation-level inputs (`mp_supported_backends`, the role's tunables like `mp_kubevirt_wait_timeout`). Per-host validation (the `mp.<backend>` block exists on each host, has `image`, etc.) is done with explicit `assert` blocks at the top of each role's `tasks/<phase>.yml`, looping over `groups['molecule']`. This matches v1's approach for `molecule_yml.platforms` — `argument_specs` does not validate inside loops.

## Self-test scenario and CI

### Single self-test scenario

Replace `extensions/molecule/podman/` and `extensions/molecule/kubevirt/` with a single `extensions/molecule/default/` containing both backends' `mp.<backend>` blocks per host:

```
extensions/molecule/default/
├── molecule.yml
├── create.yml
├── destroy.yml
├── prepare.yml
├── converge.yml
├── verify.yml
└── inventory/
    ├── hosts.yml
    └── group_vars/
        └── molecule.yml
```

`hosts.yml` has two hosts (`instance-1`, `instance-2`), each with `mp.podman` and `mp.kubevirt` blocks. CI runs the same scenario twice, switching `PROVISIONER`, which exercises the dual-backend property — something the v1 self-tests did not verify.

### CI jobs

Replace `integration` + `kubevirt` jobs in `.github/workflows/tests.yml` with:

| Job | Recipe |
| --- | --- |
| `integration-podman` | Today's `integration` job, but `PROVISIONER=podman` and `pytest tests/integration -v -k default`. |
| `integration-kubevirt` | Today's `kubevirt` job (kind cluster, KubeVirt operator with `useEmulation`, namespace creation, VM watcher, diagnostics, `addopts=""` override for visible PLAY RECAP), but `PROVISIONER=kubevirt` and `-k default`. |

`all_green.needs` is updated to reference the new job names. Two jobs (rather than `strategy.matrix`) keeps kubevirt-specific setup steps free of `if: matrix.provisioner == 'kubevirt'` clutter.

## File-level summary

### Modified

| File | Change |
| --- | --- |
| `playbooks/{create,destroy,prepare}.yml` | Replace env-var lookup with `mp_backend` group-var lookup; replace `molecule_yml.platforms` validation with `groups['molecule']` validation. |
| `playbooks/group_vars/all.yml` (new file or existing) | Add `mp_supported_backends: [podman, kubevirt]`. |
| `roles/podman/tasks/create.yml` | Loop replacement; remove "build hosts dict / write molecule group" block; emit only `ansible_connection: containers.podman.podman` per host into the runtime inventory file. |
| `roles/podman/tasks/{destroy,prepare,_networks}.yml` | Loop and field-resolution replacements. |
| `roles/kubevirt/tasks/create.yml` | Loop replacement; trim inventory write to only emit runtime connection vars. |
| `roles/kubevirt/tasks/{destroy,prepare,_create_vm,_create_vm_dictionary}.yml` | Loop and field-resolution replacements; standardize `loop_var: item` (was `vm`). |
| `roles/podman/defaults/main.yml`, `roles/kubevirt/defaults/main.yml` | Add per-field defaults from the schema tables. |
| `roles/podman/meta/argument_specs.yml`, `roles/kubevirt/meta/argument_specs.yml` | Drop platform-shape validation; describe role-level inputs only. |
| `roles/podman/README.md`, `roles/kubevirt/README.md` | Replace platform-schema docs with `mp.<backend>.*` schema. |
| `.github/workflows/tests.yml` | `integration` → `integration-podman`, `kubevirt` → `integration-kubevirt`; both run `pytest -k default`; update `all_green.needs`. |
| `docs/MIGRATION.md` | Repurpose: documents field-by-field translation from molecule v1 platforms-shape to this collection's ansible-native shape. (Original devhost-style content removed.) |
| `CLAUDE.md` | Update Architecture, Public-contract, Common-commands, lint, and CI sections to reflect v2 shape. The "Do not depend on `molecule-plugins`" section is unchanged. |
| `changelogs/fragments/<name>.yml` | New fragment under `major_changes:` describing the ansible-native shape as the v1.0 design. |

### Added

| File | Purpose |
| --- | --- |
| `extensions/molecule/default/` (and contents) | Single self-test scenario covering both backends. |
| `docs/examples/molecule.yml` | The boilerplate `molecule.yml` consumers copy. |
| `docs/examples/inventory/hosts.yml` | Inventory shape example. |
| `docs/examples/inventory/group_vars/molecule.yml` | `mp_backend` + `mp_defaults` example. |

### Deleted

| File | Reason |
| --- | --- |
| `extensions/molecule/podman/` | Superseded by `extensions/molecule/default/`. |
| `extensions/molecule/kubevirt/` | Superseded by `extensions/molecule/default/`. |
| `docs/examples/platforms.yml` | Superseded by the `inventory/` + `molecule.yml` examples. |

### Unchanged

- `playbooks/{create,destroy,prepare}.yml` overall structure (still localhost dispatcher → `include_role` with `tasks_from`).
- `roles/<backend>/tasks/_*.yml` helper task file boundaries — only their loop variables and field-resolution expressions change.
- `galaxy.yml` (version stays `1.0.0`; this is pre-release reshape, not a bump).
- `requirements.txt`, `test-requirements.txt`.
- `.ansible-lint`, `.yamllint`, `.pre-commit-config.yaml`.
- `.github/workflows/release.yml`.
- `tests/integration/test_integration.py` (the `pytest_ansible.molecule_scenario` fixture discovers scenarios automatically; only the CI `-k` selector changes, and that lives in `tests.yml`).

## Versioning posture

`1.0.0` is unreleased and has no Galaxy publication or git tag. The ansible-native shape becomes v1.0's release shape directly. There is no v0.x → v1.0 transition and no v1 platforms shape to deprecate. The changelog fragment describes this as the initial v1.0 design.

Future semver:
- Adding a new optional `mp.<backend>.*` field → minor (v1.x.0).
- Renaming or removing a field, changing a default value visibly, or dropping a backend → major (v2.0.0).
- Bug fixes, role internals, CI changes → patch.
