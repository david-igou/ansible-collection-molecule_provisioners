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

Three top-level dispatcher playbooks (`playbooks/{create,destroy,prepare}.yml`) read `$PROVISIONER` (default `podman`), validate platform shape, and `include_role` into one of two roles (`roles/podman`, `roles/kubevirt`). Each role uses `tasks_from` for lifecycle dispatch. Consumers' scenario `create.yml`/`destroy.yml`/`prepare.yml` are one-liners that `import_playbook: david_igou.molecule_provisioners.<phase>`. Public contract: each platform in `molecule.yml` has multi-keyed `podman:` and/or `kubevirt:` blocks; same `molecule.yml` works under either backend by switching `$PROVISIONER`.

### Key files

- `playbooks/{create,destroy,prepare}.yml` — dispatcher entry points; the `import_playbook` targets that consumers reference by FQCN.
- `roles/podman/tasks/{create,destroy,prepare,_networks}.yml` — podman lifecycle. `_networks.yml` is shared between create and destroy.
- `roles/kubevirt/tasks/{create,destroy,prepare,_create_vm,_create_vm_dictionary}.yml` — kubevirt lifecycle. `_create_vm*.yml` are per-platform helpers included in a loop with `loop_var: vm`.
- `extensions/molecule/{podman,kubevirt}/` — self-test scenarios. Discovered by `pytest_ansible.molecule_scenario` fixture in `tests/integration/test_integration.py`. The kubevirt scenario is cluster-agnostic — it talks to whatever `KUBECONFIG` points at, as long as KubeVirt is installed there. CI provisions kind + KubeVirt with `useEmulation` before running it.
- `docs/examples/` — copy-paste starter for consumers.
- `docs/MIGRATION.md` — converting devhost-style consumers.

## Common commands

| Task | Command |
| --- | --- |
| Install runtime/test deps | `pip install -r requirements.txt -r test-requirements.txt` |
| Lint everything | `ansible-lint && yamllint .` |
| Run podman self-test | `pytest tests/integration -v -k podman` |
| Run kubevirt self-test (requires `$KUBECONFIG` pointing at a cluster with KubeVirt) | `pytest tests/integration -v -k kubevirt` |
| Run a single scenario directly | `cd extensions/molecule/<podman\|kubevirt> && molecule test` |
| Ansible sanity | `ansible-test sanity --docker` (run from the symlink path) |
| Build collection artifact | `ansible-galaxy collection build` |
| Pre-commit | `pre-commit run --all-files` |

`pyproject.toml` configures pytest with `-n 2` (xdist parallel). The `kubevirt` CI job overrides this with `-o addopts="" -s` so molecule's `PLAY RECAP` output is visible in the runner log — xdist captures stdout per-worker, which made it impossible to tell whether the scenario was actually exercising the lifecycle.

## Public contract (the thing we don't break without a major bump)

The platform schema in `molecule.yml`:

```yaml
platforms:
  - name: <str>                 # required
    podman:                     # required when PROVISIONER=podman
      image: <str>              # required
      # optional: command, privileged, volumes, capabilities,
      # podman_network, env, tmpfs, exposed_ports, published_ports
    kubevirt:                   # required when PROVISIONER=kubevirt
      image: <str>              # required (containerdisk)
      namespace: <str>          # required
      ansible_user: <str>       # required
      memory: <str>             # required
      disk_size: <str>          # required
      ssh_service:
        type: NodePort          # only NodePort in v1
```

Breaking changes to the above keys → major version bump. New optional keys → minor.

## When updating provisioner logic

1. Make changes in the role (`roles/<backend>/tasks/`).
2. Run `ansible-lint roles/<backend>/`.
3. Run the self-test scenario: `cd extensions/molecule/<backend> && molecule test`.
4. If the change affects the platform schema, also update:
   - `roles/<backend>/meta/argument_specs.yml`
   - `roles/<backend>/README.md`
   - `docs/examples/platforms.yml`
   - the schema section above

## Lint conventions

`.ansible-lint` skips `var-naming[no-role-prefix]` because the collection uses an `mp_*` prefix on user-facing variables (collection-wide), which lint expects to be role-prefixed (`podman_*`, `kubevirt_*`). The collection-wide prefix is intentional — it makes vars discoverable across both roles.

`ansible-lint` 26.4+ requires `name:` on every play-level entry, including `import_playbook`. All scenario lifecycle one-liners and `docs/examples/` files include short imperative names.

`.yamllint` raises the line-length limit to 120 (default 80) — long URLs in `galaxy.yml` and Jinja expressions in roles routinely exceed 80.

## Pre-commit

Runs `update-docs` (collection_prep), `prettier`, `isort`, `black`, `flake8`, plus `no-commit-to-branch` against `main`. Don't bypass with `--no-verify`.

## CI

`.github/workflows/tests.yml` runs the reusable workflows from `ansible/ansible-content-actions` (changelog, build-import, ansible-lint, sanity, unit-galaxy) plus `unit-source`, an `integration` job that exercises the podman scenario via pytest, and a `kubevirt` job that exercises the kubevirt scenario on an in-CI kind cluster with KubeVirt in `useEmulation` mode. `release.yml` publishes to Galaxy on GitHub release.

## Out of scope (per the v1.0 spec)

docker / qemu / libvirt / cloud backends, LoadBalancer kubevirt service types, Windows guests, Molecule `shared_state` pattern. See `docs/superpowers/specs/2026-05-08-molecule-provisioners-design.md` for the design discussion.
