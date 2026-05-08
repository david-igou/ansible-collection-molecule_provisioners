# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project type

Ansible Collection `david_igou.molecule_provisioners`. Provides reusable Molecule provisioner playbooks and roles (podman, kubevirt) so other collections can test themselves without copy-pasting `create.yml`/`destroy.yml`/`prepare.yml` per repo. Targets `ansible-core >= 2.15`.

The collection FQCN appears throughout (`david_igou.molecule_provisioners.create`, etc.). Tooling requires it to live at `ansible_collections/david_igou/molecule_provisioners/` somewhere on `ANSIBLE_COLLECTIONS_PATH`. If working outside that layout, symlink the repo into Ansible's default search path:

```bash
mkdir -p "$HOME/.ansible/collections/ansible_collections/david_igou"
ln -snf "$PWD" "$HOME/.ansible/collections/ansible_collections/david_igou/molecule_provisioners"
```

`ansible-galaxy collection list` should then show `david_igou.molecule_provisioners 1.0.0` without setting `ANSIBLE_COLLECTIONS_PATH`.

## Architecture (one-paragraph version)

Three top-level dispatcher playbooks (`playbooks/{create,destroy,prepare}.yml`) read `mp_backend` from the molecule group's hostvars (`hostvars[groups['molecule'][0]].mp_backend`), validate the inventory shape, and `include_role` into one of two roles (`roles/podman`, `roles/kubevirt`). Each role uses `tasks_from` for lifecycle dispatch and starts with a 3-level merge (role defaults <- `mp_defaults.<backend>` <- `hostvars[item].mp.<backend>`) before looping `groups['molecule']`. Consumers' scenario `create.yml`/`destroy.yml`/`prepare.yml` are one-liners that `import_playbook: david_igou.molecule_provisioners.<phase>`. The molecule.yml itself uses molecule's ansible-native shape (`ansible:` block — no `driver:`, no `platforms:`, no `provisioner:`).

### Key files

- `playbooks/{create,destroy,prepare}.yml` — dispatcher entry points; the `import_playbook` targets that consumers reference by FQCN.
- `playbooks/group_vars/all.yml` — declares `mp_supported_backends`.
- `roles/podman/tasks/{create,destroy,prepare,_networks}.yml` — podman lifecycle. `_networks.yml` is shared between create and destroy.
- `roles/kubevirt/tasks/{create,destroy,prepare,_create_vm,_create_vm_dictionary}.yml` — kubevirt lifecycle. `_create_vm*.yml` are per-host helpers included in a loop over `groups['molecule']`.
- `roles/<backend>/defaults/main.yml` — role-level defaults including the `mp_<backend>_role_defaults` dict that feeds the merge.
- `extensions/molecule/default/` — single self-test scenario carrying both backends' specs per host. Discovered by `pytest_ansible.molecule_scenario` fixture in `tests/integration/test_integration.py`. The kubevirt-backend run is cluster-agnostic — it talks to whatever `KUBECONFIG` points at, as long as KubeVirt is installed there. CI provisions kind + KubeVirt with `useEmulation` before running it.
- `docs/examples/` — copy-paste starter for consumers (`molecule.yml` boilerplate + `inventory/` shape).
- `docs/MIGRATION.md` — translating from molecule's pre-ansible-native `platforms:` shape to this collection.

## Do not depend on `molecule-plugins`

This collection must never list `molecule-plugins` (or any of its extras like `molecule-plugins[podman]`, `molecule-plugins[kubevirt]`) in `requirements.txt`, `test-requirements.txt`, CI install steps, or scenario `molecule.yml` `driver:` blocks. Both scenarios use `driver: name: default` and delegate the lifecycle to the playbooks shipped here — the whole point of the collection is to replace those plugins, not consume them. If you copy a CI step from another repo and it pulls `molecule-plugins`, strip it.

## Common commands

| Task | Command |
| --- | --- |
| Install runtime/test deps | `pip install -r requirements.txt -r test-requirements.txt` |
| Lint everything | `ansible-lint && yamllint .` |
| Run podman self-test | `PROVISIONER=podman pytest tests/integration -v -k default` |
| Run kubevirt self-test (requires `$KUBECONFIG` pointing at a cluster with KubeVirt) | `PROVISIONER=kubevirt pytest tests/integration -v -k default` |
| Run a single scenario directly | `cd extensions/molecule/default && PROVISIONER=<backend> molecule test` |
| Ansible sanity | `ansible-test sanity --docker` (run from the symlink path) |
| Build collection artifact | `ansible-galaxy collection build` |
| Pre-commit | `pre-commit run --all-files` |

`pyproject.toml` configures pytest with `-n 2` (xdist parallel). The `kubevirt` CI job overrides this with `-o addopts="" -s` so molecule's `PLAY RECAP` output is visible in the runner log — xdist captures stdout per-worker, which made it impossible to tell whether the scenario was actually exercising the lifecycle.

## Public contract (the thing we don't break without a major bump)

The inventory shape consumers ship:

```yaml
all:
  children:
    molecule:
      hosts:
        <name>:
          mp:
            podman:                     # required when mp_backend == podman
              image: <str>              # required
              # optional: command, privileged, volumes, capabilities,
              # podman_network, env, tmpfs, exposed_ports, published_ports
            kubevirt:                   # required when mp_backend == kubevirt
              image: <str>              # required (containerdisk)
              namespace: <str>          # optional, role default 'molecule'
              ssh_user: <str>           # optional, role default 'cloud-user'
              memory: <str>             # optional, role default '1Gi'
              ssh_service:
                type: NodePort          # optional, only NodePort in v1
```

Plus:
- `inventory/group_vars/molecule.yml` must define `mp_backend` (one of `mp_supported_backends`).
- `mp_defaults.<backend>.<field>` is an optional group-var layer between role defaults and per-host hostvars.
- `molecule.yml` uses molecule's ansible-native shape (`ansible:` block).

Breaking changes to the above keys → major version bump. New optional fields → minor.

## When updating provisioner logic

1. Make changes in the role (`roles/<backend>/tasks/`).
2. Run `ansible-lint roles/<backend>/`.
3. Run the self-test scenario: `cd extensions/molecule/default && PROVISIONER=<backend> molecule test`.
4. If the change affects the per-host schema, also update:
   - `roles/<backend>/defaults/main.yml` (`mp_<backend>_role_defaults`)
   - `roles/<backend>/meta/argument_specs.yml`
   - `roles/<backend>/README.md`
   - `docs/examples/inventory/hosts.yml` (and `group_vars/molecule.yml` if a default value moves)
   - the schema section above

## Lint conventions

`.ansible-lint` skips `var-naming[no-role-prefix]` because the collection uses an `mp_*` prefix on user-facing variables (collection-wide), which lint expects to be role-prefixed (`podman_*`, `kubevirt_*`). The collection-wide prefix is intentional — it makes vars discoverable across both roles.

`ansible-lint` 26.4+ requires `name:` on every play-level entry, including `import_playbook`. All scenario lifecycle one-liners and `docs/examples/` files include short imperative names.

`.yamllint` raises the line-length limit to 120 (default 80) — long URLs in `galaxy.yml` and Jinja expressions in roles routinely exceed 80.

## Pre-commit

Runs `update-docs` (collection_prep), `prettier`, `isort`, `black`, `flake8`, plus `no-commit-to-branch` against `main`. Don't bypass with `--no-verify`.

## CI

`.github/workflows/tests.yml` runs the reusable workflows from `ansible/ansible-content-actions` (changelog, build-import, ansible-lint, sanity, unit-galaxy) plus `unit-source`, an `integration-podman` job that exercises the default scenario via pytest with `PROVISIONER=podman`, and an `integration-kubevirt` job that exercises the same scenario with `PROVISIONER=kubevirt` on an in-CI kind cluster with KubeVirt in `useEmulation` mode. `release.yml` publishes to Galaxy on GitHub release.

## Out of scope (per the v1.0 spec)

docker / qemu / libvirt / cloud backends, LoadBalancer kubevirt service types, Windows guests, Molecule `shared_state` pattern. See `docs/superpowers/specs/2026-05-08-molecule-provisioners-design.md` for the design discussion.
