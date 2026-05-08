# `david_igou.molecule_provisioners` — Reusable Molecule provisioners as a collection

**Status:** Approved (brainstorm complete, awaiting implementation plan)
**Date:** 2026-05-08

## Problem

When writing Molecule tests for an Ansible collection, the provisioning automation (`create.yml`/`destroy.yml`/`prepare.yml`) has to be redefined per repo. The reference example — `david-igou/ansible-collection-devhost`'s `extensions/molecule/provisioners/{podman,kubevirt}/` — works, but every change to that automation has to be hand-applied across every consuming repo.

The goal: package this provisioning automation as an Ansible collection so consumers depend on it, and their per-scenario lifecycle files become one-line wrappers that import collection-shipped playbooks.

## Solution overview

Each consuming scenario keeps three tiny files:

```yaml
# extensions/molecule/<scenario>/create.yml
- import_playbook: david_igou.molecule_provisioners.create
```

```yaml
# extensions/molecule/<scenario>/destroy.yml
- import_playbook: david_igou.molecule_provisioners.destroy
```

```yaml
# extensions/molecule/<scenario>/prepare.yml
- import_playbook: david_igou.molecule_provisioners.prepare
```

The collection ships those three top-level playbooks plus the per-backend roles that actually do the work. The active backend is selected at runtime via `$PROVISIONER` (default `podman`).

This is the only mechanism that fits Molecule's path-resolution rule: `provisioner.playbooks.create/destroy/prepare` resolve relative to the scenario directory, so the indirection has to be a one-line scenario playbook that uses Ansible's collection-FQCN `import_playbook` (Ansible 2.11+).

## Architecture

```
david_igou.molecule_provisioners/
├── galaxy.yml                       deps: containers.podman, kubernetes.core, community.crypto
├── meta/runtime.yml                 requires_ansible: ">=2.15.0"
├── playbooks/
│   ├── create.yml                   hosts: localhost; dispatcher
│   ├── destroy.yml                  hosts: localhost; dispatcher
│   └── prepare.yml                  hosts: all;       dispatcher
├── roles/
│   ├── podman/
│   │   ├── tasks/{main.yml,create.yml,destroy.yml,prepare.yml,_networks.yml}
│   │   ├── defaults/main.yml
│   │   └── meta/argument_specs.yml
│   └── kubevirt/
│       ├── tasks/{main.yml,create.yml,destroy.yml,prepare.yml,
│       │           _create_vm.yml,_create_vm_dictionary.yml}
│       ├── defaults/main.yml
│       └── meta/argument_specs.yml
├── docs/examples/
│   ├── molecule.yml                 starter template consumers copy
│   ├── create.yml                   one-liner
│   ├── destroy.yml                  one-liner
│   ├── prepare.yml                  one-liner
│   └── platforms.yml                full platform schema documented inline
├── docs/MIGRATION.md                step-by-step for devhost-style consumers
└── extensions/molecule/
    ├── podman/                      self-test scenario (CI default)
    └── kubevirt/                    self-test scenario (gated; needs cluster)
```

### Why one role per provisioner with `tasks_from` entry points

Each provisioner is one conceptual thing (`podman`, `kubevirt`) with three lifecycle operations on it. Splitting into six lifecycle-named roles (`podman_create`, `podman_destroy`, ...) inflates README/argument_specs boilerplate and forces shared logic (e.g., `__podman_networks` collection used by both create and destroy) into awkward `vars/` hops. A single mega-role with provisioner-as-variable conflates two backends inside one task tree and prevents per-backend `argument_specs`. One role per provisioner with `tasks_from` is the cleanest fit.

### Dispatcher pattern

`playbooks/create.yml` (and analogously `destroy.yml`, `prepare.yml`):

```yaml
- name: Create molecule instances
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    _mp_provisioner: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"
  tasks:
    - name: Validate provisioner choice
      ansible.builtin.assert:
        that: _mp_provisioner in ['podman', 'kubevirt']
        fail_msg: "PROVISIONER must be 'podman' or 'kubevirt' (got '{{ _mp_provisioner }}')"
    - name: Validate every platform has the active provisioner's key
      ansible.builtin.assert:
        that: item[_mp_provisioner] is defined
        fail_msg: "Platform '{{ item.name }}' is missing the '{{ _mp_provisioner }}' block"
      loop: "{{ molecule_yml.platforms }}"
      loop_control:
        label: "{{ item.name }}"
    - name: Run provisioner create
      ansible.builtin.include_role:
        name: "david_igou.molecule_provisioners.{{ _mp_provisioner }}"
        tasks_from: create
```

`prepare.yml` is identical except `hosts: all`.

## Public contract

### Platform schema (consumer's `molecule.yml`)

Each platform entry is multi-keyed; the active backend's key is required, the other is optional but recommended so the same `molecule.yml` works under either `PROVISIONER`.

```yaml
platforms:
  - name: ubuntu-24                   # required (instance/host name)
    podman:                           # required when PROVISIONER=podman
      image: docker.io/...:latest     # required
      command: sleep 1d               # optional
      privileged: false               # optional, default false
      volumes: []                     # optional
      capabilities: []                # optional
      podman_network: []              # optional, list or string
      env: {}                         # optional
      tmpfs: []                       # optional
      exposed_ports: []               # optional
      published_ports: []             # optional
    kubevirt:                         # required when PROVISIONER=kubevirt
      image: quay.io/...              # required (containerdisk image)
      namespace: molecule             # required
      ansible_user: cloud-user        # required
      memory: 4Gi                     # required
      disk_size: 30Gi                 # required
      ssh_service:
        type: NodePort                # only NodePort supported in v1
```

### Backend selection

`$PROVISIONER` env var, evaluated at dispatcher runtime. Default `podman` when unset/empty. Only `podman` and `kubevirt` are accepted in v1; anything else fails fast in the dispatcher's first `assert`.

### Molecule driver mode

The design assumes the consumer uses Molecule's `driver: name: default` with `options.managed: true` (the same mode devhost uses today). Under that mode Molecule auto-generates inventory from `platforms[].name`, which is what podman relies on. We deliberately do **not** adopt Molecule's newer `shared_state` / shared-default-scenario pattern in v1 — see "Out of scope".

### Inventory contract

Both backends produce a host group named `molecule` containing all platform hosts.

- **podman**: relies on Molecule's auto-inventory generation from `platforms[].name`. No inventory writing in the role.
- **kubevirt**: explicitly writes `$MOLECULE_EPHEMERAL_DIRECTORY/inventory/molecule_inventory.yml` (SSH host/port come from the K8s NodePort service after VM creation), then `meta: refresh_inventory`, then asserts `'molecule' in groups`.

### Consumer-overridable variables

Precedence: scenario `group_vars` → `molecule.yml` `provisioner.env` → role defaults.

- `mp_default_provisioner` (default `podman`) — fallback when `$PROVISIONER` unset.
- `mp_kubevirt_namespace` (default `molecule`) — also overridable per-platform via `kubevirt.namespace`.
- `mp_kubevirt_ssh_key_path` (default `{{ molecule_ephemeral_directory }}/identity_file`).

All of these are documented in each role's `meta/argument_specs.yml` so `ansible-lint --profile=production` validates them.

## Lifecycle data flow

```
molecule create
  → scenario create.yml
    → import_playbook david_igou.molecule_provisioners.create
      → reads $PROVISIONER (default podman)
      → asserts every platform has the active key
      → include_role <provisioner> tasks_from=create
        → podman: create networks (when set); create containers async; wait
        → kubevirt: gen SSH keypair; create VMs; create NodePort services;
                    build inventory dict; write molecule_inventory.yml;
                    refresh_inventory; assert molecule group exists

molecule prepare
  → scenario prepare.yml → collection.prepare → role tasks_from=prepare
    → podman: install sudo
    → kubevirt: wait_for_connection (timeout 120s)

molecule destroy
  → scenario destroy.yml → collection.destroy → role tasks_from=destroy
    → podman: remove containers async; wait; delete networks (when set)
    → kubevirt: delete VirtualMachine; delete NodePort Service
```

## Error handling & validation

| Failure | Where | Behavior |
| --- | --- | --- |
| `$PROVISIONER` not in `{podman, kubevirt}` | dispatcher, first task | `assert` fails with explicit message |
| Platform missing the active provisioner's key | dispatcher, second task | `assert` fails listing the offending names |
| `kubevirt.ssh_service.type` ≠ `NodePort` | kubevirt role, create task | `assert` fails: "v1 supports only ssh_service.type=NodePort" |
| Required kubevirt fields missing | role `argument_specs` | role-arg validation fails before any cluster call |
| Required podman field (`image`) missing | role `argument_specs` | same |
| K8s API unreachable / podman socket missing | underlying module | propagates; not wrapped |
| Network/cleanup failures during destroy | role destroy task | fail loudly; destroy is idempotent (`state: absent`) so re-runs are safe |

We do not swallow errors. Provisioner playbooks failing visibly is the right behavior in CI.

### Idempotency requirements

- `create` re-runnable without re-creating resources (already true: `containers.podman.podman_container: recreate: false` and `kubernetes.core.k8s: state: present`).
- `destroy` succeeds when resources are already gone (`state: absent` is naturally idempotent).
- `prepare` succeeds when prep already done (installing a present package, waiting for a reachable host — both idempotent today).

## Testing

The collection self-tests via the existing `pytest tests/integration` machinery. `tests/integration/test_integration.py` discovers each `extensions/molecule/<scenario>/` and runs `molecule test` per scenario via the `pytest_ansible.molecule.MoleculeScenario` fixture.

| Scenario | Provisioner | Runs in CI? | Why |
| --- | --- | --- | --- |
| `extensions/molecule/podman/` | podman | yes | podman runs in GH Actions runners |
| `extensions/molecule/kubevirt/` | kubevirt | no (gated) | needs a live KubeVirt-enabled cluster |

CI gating: kubevirt scenario is skipped when `MOLECULE_KUBEVIRT_ENABLED` is unset (the default). Locally, `MOLECULE_KUBEVIRT_ENABLED=1 pytest tests/integration` runs both. Mechanism: a small `tests/integration/conftest.py` filters the `molecule_scenario` parameterization by env var.

Each self-test scenario:

1. Creates ~2 platforms (one Ubuntu, one CentOS Stream — matches devhost's spread).
2. Runs `prepare`.
3. `converge.yml` and `verify.yml` are minimal — assert reachability and that the `molecule` group has the expected hosts. The collection isn't testing applications; it tests that the provisioner produced a working host group.
4. Runs `destroy`.

`ansible-test sanity --docker` and `ansible-lint --profile=production` continue to run via the existing reusable workflows (`.github/workflows/tests.yml`).

### Pre-release manual gate

Before tagging v1.0: take a copy of devhost, replace its `extensions/molecule/provisioners/` with three one-liner files, and run `PROVISIONER=podman molecule test` from `extensions/molecule/default/`. If that passes unmodified, the contract holds.

## Repo cleanup

**Delete:**
- `plugins/action/sample_action.py`, `plugins/filter/sample_filter.py`, `plugins/lookup/sample_lookup.py`, `plugins/test/sample_test.py`, `plugins/modules/sample_*.py`
- Empty plugin subdirs not used by this collection: `cache/`, `inventory/`, `module_utils/`, `plugin_utils/`, `sub_plugins/`
- `roles/run/`
- `extensions/molecule/integration_hello_world/`
- `tests/integration/targets/hello_world/`

**Also delete:**
- `extensions/molecule/utils/` — its shared converge stripped an `integration_` prefix that no longer applies. The two self-test scenarios (`podman/`, `kubevirt/`) each carry their own minimal `converge.yml` and `verify.yml` inline.

**Keep:**
- `tests/integration/test_integration.py` + `pytest_ansible.molecule` plumbing (extended with the conftest-based env gate above).

## `galaxy.yml` updates

```yaml
description: Reusable Molecule provisioner playbooks/roles for testing Ansible collections
tags: [molecule, testing, podman, kubevirt]
dependencies:
  containers.podman: ">=1.10.0"
  kubernetes.core: ">=3.0.0"
  community.crypto: ">=2.0.0"
```

Drop `ansible.utils` (unused by either provisioner). Pin lower bounds, leave upper open.

Also fill in `authors`, `repository`, `documentation`, `homepage`, `issues` (currently placeholders from `ansible-creator`).

## Documentation surface

| File | Purpose |
| --- | --- |
| `README.md` | Replace boilerplate with: what this is, the 3 one-liner files, link to `docs/examples/`, `$PROVISIONER` toggle, supported backends |
| `docs/examples/{molecule,create,destroy,prepare}.yml` | Copy-paste starter scenario |
| `docs/examples/platforms.yml` | Full platform schema documented inline |
| `roles/podman/README.md`, `roles/kubevirt/README.md` | Required/optional inputs (matches `argument_specs.yml`) |
| `CHANGELOG.rst` + `changelogs/fragments/` | Existing `antsibull-changelog` flow |
| `CLAUDE.md` | Update to describe the new architecture (replaces the current scaffold-oriented description) |
| `docs/MIGRATION.md` | Verbatim steps for converting a devhost-style consumer |

## Release flow

- First tag is `v1.0.0`. `galaxy.yml` already has version `1.0.0`.
- Existing `.github/workflows/release.yml` (`ansible-content-actions/release_galaxy.yaml`) publishes to Galaxy on GitHub release published — no change needed.
- Existing `.github/workflows/tests.yml` runs sanity / ansible-lint / unit-galaxy / unit-source / build-import. **Add** a new job that runs `pytest tests/integration` with `PROVISIONER=podman` and `MOLECULE_KUBEVIRT_ENABLED` unset.

## Migration story

For each consumer (devhost first):

1. Add `david_igou.molecule_provisioners` to the project's `requirements.yml`.
2. In each scenario directory, replace the old provisioners-tree references with three one-line files:
   ```yaml
   # extensions/molecule/<scenario>/create.yml
   - import_playbook: david_igou.molecule_provisioners.create
   ```
   (likewise `destroy.yml`, `prepare.yml`).
3. Update each scenario's `molecule.yml`:
   - Repoint `dependency.options.requirements-file` at the consumer's top-level `requirements.yml` (which now lists `david_igou.molecule_provisioners`). Transitive deps — `containers.podman`, `kubernetes.core`, `community.crypto` — are pulled in by the collection's `galaxy.yml`. Alternatively, drop the `dependency` block entirely and rely on the consumer's CI step `ansible-galaxy collection install -r requirements.yml` before molecule runs.
   - Change `provisioner.playbooks.create` from `../provisioners/${PROVISIONER:-podman}/create.yml` to `create.yml`. Same for `destroy`, `prepare`.
   - Remove `inventory.links.group_vars` entry (collection roles handle their own group vars internally).
4. Delete the entire `extensions/molecule/provisioners/` tree from the consumer repo.
5. `PROVISIONER=podman molecule test` from each scenario dir to verify.

`docs/MIGRATION.md` captures these steps verbatim.

## Versioning

This is v1.0 of the collection. The platform schema (`podman.*`, `kubevirt.*` keys) is the public contract.

- Breaking change to existing keys → major version bump.
- New optional keys → minor version bump.
- Bug fixes / role internals → patch.

## Out of scope for v1.0

Called out explicitly in README so consumers don't ask:

- docker, qemu/libvirt, AWS, Azure, GCP backends
- LoadBalancer / ClusterIP+port-forward kubevirt service types
- Windows/macOS guests
- Per-platform networks beyond `podman.podman_network`
- Molecule `shared_state` / shared default-scenario pattern (deferred — current design uses copy-paste starter template instead)
- Switching consumers off `driver: default, managed: true` to a fully ansible-native shared inventory model
