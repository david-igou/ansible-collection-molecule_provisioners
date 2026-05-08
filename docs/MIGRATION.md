# Migrating from a per-repo `extensions/molecule/provisioners/` tree

If your collection currently has `extensions/molecule/provisioners/{podman,kubevirt}/{create,destroy,prepare,requirements}.yml` (the pattern from `david-igou/ansible-collection-devhost`), here's how to switch to using `david_igou.molecule_provisioners`.

## Before

```
your-collection/
└── extensions/molecule/
    ├── provisioners/
    │   ├── podman/
    │   │   ├── create.yml
    │   │   ├── destroy.yml
    │   │   ├── prepare.yml
    │   │   ├── requirements.yml
    │   │   └── group_vars/...
    │   └── kubevirt/
    │       ├── create.yml
    │       ├── destroy.yml
    │       ├── prepare.yml
    │       ├── requirements.yml
    │       └── group_vars/...
    └── default/
        ├── molecule.yml          (points at ../provisioners/${PROVISIONER:-podman}/...)
        ├── converge.yml
        └── verify.yml
```

## After

```
your-collection/
├── requirements.yml              (now lists david_igou.molecule_provisioners)
└── extensions/molecule/default/
    ├── create.yml                (one-liner)
    ├── destroy.yml               (one-liner)
    ├── prepare.yml               (one-liner)
    ├── molecule.yml              (points at create.yml/destroy.yml/prepare.yml)
    ├── converge.yml
    └── verify.yml
```

## Steps

### 1. Add the dependency

In your `requirements.yml`:

```yaml
collections:
  - name: david_igou.molecule_provisioners
    version: ">=1.0.0,<2.0.0"
```

Transitive deps (`containers.podman`, `kubernetes.core`, `community.crypto`) are pulled in automatically by the collection's `galaxy.yml` — you can remove the per-provisioner `requirements.yml` files.

### 2. Replace each scenario's lifecycle files

For every directory under `extensions/molecule/<scenario>/`:

```yaml
# create.yml
---
- name: Create instances
  import_playbook: david_igou.molecule_provisioners.create
```

```yaml
# destroy.yml
---
- name: Destroy instances
  import_playbook: david_igou.molecule_provisioners.destroy
```

```yaml
# prepare.yml
---
- name: Prepare instances
  import_playbook: david_igou.molecule_provisioners.prepare
```

The `name:` keys are required by `ansible-lint`'s `name[play]` rule (26.4+).

### 3. Update each scenario's `molecule.yml`

- Repoint `dependency.options.requirements-file` at your top-level `requirements.yml` (or remove the `dependency` block and rely on a CI step `ansible-galaxy collection install -r requirements.yml`).
- Change the playbook references:
  ```yaml
  provisioner:
    name: ansible
    playbooks:
      create: create.yml          # was: ../provisioners/${PROVISIONER:-podman}/create.yml
      destroy: destroy.yml        # was: ../provisioners/${PROVISIONER:-podman}/destroy.yml
      prepare: prepare.yml        # was: ../provisioners/${PROVISIONER:-podman}/prepare.yml
      converge: converge.yml
      verify: verify.yml
  ```
- Remove `inventory.links.group_vars` if it pointed at `../provisioners/<name>/group_vars/`. The collection's roles handle their own internal vars. (Keep `inventory.links.group_vars` if you have your own scenario-specific group_vars — for example, the kubevirt backend needs `ansible_connection: ssh` set somewhere.)

### 4. Delete the old tree

```bash
rm -rf extensions/molecule/provisioners/
```

### 5. Verify

```bash
ansible-galaxy collection install -r requirements.yml
PROVISIONER=podman   molecule test -s default
# If you have a KubeVirt cluster:
PROVISIONER=kubevirt molecule test -s default
```

Both should pass without further changes. If they don't, your platform schema may be missing required keys — check `docs/examples/platforms.yml` in this collection for the canonical reference.

## Platform schema differences from the legacy provisioners tree

The collection's platform schema is **identical** to what devhost uses today (multi-keyed `podman:` + `kubevirt:` blocks under each platform). No platform-list edits are required to migrate.
