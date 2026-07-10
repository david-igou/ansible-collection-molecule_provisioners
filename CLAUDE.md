# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project type

Ansible Collection `david_igou.molecule_provisioners`. Provides reusable Molecule provisioner playbooks and roles (podman, kubevirt) so other collections can test themselves without copy-pasting `create.yml`/`destroy.yml`/`prepare.yml` per repo. Targets `ansible-core >= 2.15`.

This project is in early alpha stages and breaking changes will be a regular occurance. There is no need to bump a version if they are being implemented.

The collection FQCN appears throughout (`david_igou.molecule_provisioners.create`, etc.). Tooling requires it to live at `ansible_collections/david_igou/molecule_provisioners/` somewhere on `ANSIBLE_COLLECTIONS_PATH`. If working outside that layout, symlink the repo into Ansible's default search path:

```bash
mkdir -p "$HOME/.ansible/collections/ansible_collections/david_igou"
ln -snf "$PWD" "$HOME/.ansible/collections/ansible_collections/david_igou/molecule_provisioners"
```

`ansible-galaxy collection list` should then show `david_igou.molecule_provisioners 1.0.0` without setting `ANSIBLE_COLLECTIONS_PATH`.

## Architecture (one-paragraph version)

Three top-level dispatcher playbooks (`playbooks/{create,destroy,prepare}.yml`) read `mp_backend` from the molecule group's hostvars (`hostvars[groups['molecule'][0]].mp_backend`), validate the inventory shape, and `include_role` into one of the backend roles (`roles/podman`, `roles/kubevirt`, `roles/qemu`, `roles/docker`). Each role uses `tasks_from` for lifecycle dispatch and starts with a 3-level merge (role defaults <- `mp_defaults.<backend>` <- `hostvars[item].mp.<backend>`) before looping `groups['molecule']`. Consumers' scenario `create.yml`/`destroy.yml`/`prepare.yml` are one-liners that `import_playbook: david_igou.molecule_provisioners.<phase>`. The molecule.yml itself uses molecule's ansible-native shape (`ansible:` block — no `driver:`, no `platforms:`, no `provisioner:`).

### Key files

- `playbooks/{create,destroy,prepare}.yml` — dispatcher entry points; the `import_playbook` targets that consumers reference by FQCN.
- `playbooks/reset.yml` — standalone purge playbook; removes containers labeled `owner=molecule`. Reachable as `david_igou.molecule_provisioners.reset`.
- `playbooks/group_vars/all.yml` — declares `mp_supported_backends`.
- `roles/podman/tasks/{create,destroy,prepare,_networks}.yml` — podman lifecycle. `_networks.yml` is shared between create and destroy.
- `roles/kubevirt/tasks/{create,destroy,prepare,_create_vm,_create_vm_dictionary,_build_vm,_validate}.yml` — kubevirt lifecycle. `_create_vm*.yml` are per-host helpers included in a loop over `groups['molecule']`.
- `roles/docker/tasks/{create,destroy,prepare,_spec_merge,_validate,_networks}.yml` — docker lifecycle. `_networks.yml` is shared between create and destroy.
- `roles/<backend>/defaults/main.yml` — role-level defaults including the `mp_<backend>_role_defaults` dict that feeds the merge.
- `extensions/molecule/default/` — single self-test scenario carrying both backends' specs per host. Discovered by `pytest_ansible.molecule_scenario` fixture in `tests/integration/test_integration.py`. The kubevirt-backend run is cluster-agnostic — it talks to whatever `KUBECONFIG` points at, as long as KubeVirt is installed there. CI provisions kind + KubeVirt with `useEmulation` before running it.
- `docs/examples/` — copy-paste starter for consumers: `molecule.yml` boilerplate, `inventory/` shape, plus the deterministic-setup files (`requirements-test.yml` pinned to the Galaxy version, `config.yml` wiring it into every scenario, `ansible.cfg`, and a `MOLECULE_GLOB` `Makefile`).
- `AGENTS.md` — carries the ansible-creator agents.md reference plus a one-pass determinism checklist for agents adding a scenario in a consumer repo (pin version, centralize via `config.yml`, run from root with `MOLECULE_GLOB`, commit `ansible.cfg`).
- `docs/MIGRATION.md` — translating from molecule's pre-ansible-native `platforms:` shape to this collection.

## Do not depend on `molecule-plugins`

This collection must never list `molecule-plugins` (or any of its extras like `molecule-plugins[podman]`, `molecule-plugins[kubevirt]`) in `requirements.txt`, `test-requirements.txt`, CI install steps, or scenario `molecule.yml` `driver:` blocks. Both scenarios use `driver: name: default` and delegate the lifecycle to the playbooks shipped here — the whole point of the collection is to replace those plugins, not consume them. If you copy a CI step from another repo and it pulls `molecule-plugins`, strip it.

## Common commands

| Task                                                                                | Command                                                                 |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Install runtime/test deps                                                           | `pip install -r requirements.txt -r test-requirements.txt`              |
| Lint everything                                                                     | `ansible-lint && yamllint .`                                            |
| Run podman self-test                                                                | `PROVISIONER=podman pytest tests/integration -v -k default`             |
| Run kubevirt self-test (requires `$KUBECONFIG` pointing at a cluster with KubeVirt) | `PROVISIONER=kubevirt pytest tests/integration -v -k default`           |
| Run docker self-test                                                                | `PROVISIONER=docker pytest tests/integration -v -k default`             |
| Run a single scenario directly                                                      | `cd extensions/molecule/default && PROVISIONER=<backend> molecule test` |
| Ansible sanity                                                                      | `ansible-test sanity --docker` (run from the symlink path)              |
| Build collection artifact                                                           | `ansible-galaxy collection build`                                       |
| Pre-commit                                                                          | `pre-commit run --all-files`                                            |

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
            podman: # required when mp_backend == podman
              image: <str> # required
              # optional: command, privileged, volumes, capabilities,
              # podman_network, env, tmpfs, exposed_ports, published_ports,
              # systemd, cgroupns, hostname, tty, detach, etc_hosts, dns_servers,
              # pid_mode, security_opts, devices, ulimits, ip, restart_policy,
              # restart_retries, cgroup_manager, storage_opt, storage_driver,
              # extra_opts, labels
            kubevirt: # required when mp_backend == kubevirt
              boot_source: # required: discriminated union
                type: container_disk #   container_disk | data_volume_url | data_volume_pvc | data_volume_source_ref | pvc
                image: <str> #   per-type fields; see roles/kubevirt/README.md
              namespace: <str> # optional, role default 'molecule'
              ssh_user: <str> # optional, role default 'cloud-user'
              ssh_service:
                type: NodePort # optional, 'NodePort' (default, creates Service) or 'None' (skip Service; requires connection_ip)
                port: 22 # optional, only consulted when type=None; default 22
              connection_ip: <str> # optional with NodePort, REQUIRED with None. Skips cluster-scoped Node lookup for this host
              # Optional curated knobs:
              cpu: { cores, sockets, threads, model }
              memory: <str> # role default '1Gi' → requests.memory
              memory_limit: <str> # → limits.memory
              instancetype: <str-or-dict> # str OR {name, kind}; suppresses cpu/resources
              preference: <str-or-dict>
              node_selector: <dict>
              tolerations: <list>
              affinity: <dict>
              extra_disks: <list> # appended to [containerdisk, cloudinitdisk]
              extra_volumes: <list> # appended to [containerdisk, cloudinitdisk]
              extra_interfaces: <list> # appended after default masquerade
              extra_networks: <list> # appended after default pod
              vm_overrides: <dict> # escape hatch: deep-merge into whole VM, lists append
            docker: # required when mp_backend == docker
              image: <str> # required
              # optional: command, command_handling, override_command, hostname,
              #   privileged, user, tty, pid_mode, cgroupns_mode, runtime, platform,
              #   capabilities, security_opts, sysctls, ulimits, devices,
              #   volumes, mounts, tmpfs, shm_size,
              #   networks, network_mode, networks_cli_compatible, purge_networks,
              #   dns_servers, etc_hosts, exposed_ports, published_ports, links,
              #   env, labels, restart_policy, restart_retries, stop_signal, kill_signal,
              #   memory, memory_swap, force_kill, keep_volumes
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

## Running CI locally

`act` (nektos/act) is pinned in the parent `igou-devenv` `mise.toml`. It re-plays the GitHub Actions workflow on the local container engine, which in this devcontainer is rootless podman — so a podman API socket has to be exposed first:

```bash
podman system service --time=0 unix:///tmp/podman.sock &
export DOCKER_HOST=unix:///tmp/podman.sock

act -l                                                                       # list jobs
act pull_request -j ansible-lint -P ubuntu-latest=catthehacker/ubuntu:act-22.04   # run one
```

The `-P` flag points act at a real Ubuntu runner image; the default (`node:16-slim`) is too thin for ansible tooling. `catthehacker/ubuntu:act-22.04` (~1.5 GB) is the smallest image that boots `actions/setup-python`. `act` clones the reusable workflows (`ansible/ansible-content-actions/*`, `ansible-network/github_actions/*`) on first run.

**Jobs known to work under act:** `ansible-lint`, `sanity`, `build-import`, `changelog`, `unit-galaxy` — they each run on a single runner with `actions/setup-python@v5` or no Python.

**Known limitation — `unit-source` matrix:** the upstream `ansible-network/github_actions/.github/workflows/unit_source.yml` pins `actions/setup-python@v4`, which collides with the catthehacker image's pre-installed Python and fails before any test runs (`rm: cannot remove '.../python3.12/test': Directory not empty`). Until upstream bumps to `@v5`, reproduce a single matrix cell of `unit-source` by skipping act and running pytest in a clean Python container:

```bash
# reproduce one CI unit-source cell (py3.11 + ansible-core 2.17)
podman run --rm -v "$PWD:/work" -w /work python:3.11-slim bash -c '
  pip install -q "ansible-core>=2.17,<2.18" pytest pytest-ansible pytest-xdist pyyaml
  pytest tests/unit/kubevirt_render/
'
```

Swap the version pin for `>=2.16,<2.17` to cover the collection's stated floor.

Why this matters for the kubevirt renderer: ansible-core 2.19+ preserves Python `None` through `{{ x | default(none) }}`-style templating, but 2.16/2.17 string-coerce it to `"None"`. A `_var is not none` gate that works on the devcontainer's bleeding-edge ansible-core will silently leak content into the rendered VM on the supported floor. Run at least one cell of the matrix in a clean container before pushing a renderer change.

## Out of scope (per the v1.0 spec)

libvirt / cloud backends, LoadBalancer kubevirt service types, Windows guests, Molecule `shared_state` pattern. See `docs/superpowers/specs/2026-05-08-molecule-provisioners-design.md` for the design discussion.
