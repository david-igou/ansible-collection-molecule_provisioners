# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project type

Ansible Collection `david_igou.molecule_provisioners`. Provides reusable Molecule provisioner playbooks and roles (podman, kubevirt, qemu, docker) so other collections can test themselves without copy-pasting `create.yml`/`destroy.yml`/`prepare.yml` per repo. Targets `ansible-core >= 2.15`.

This project is in alpha. Breaking changes do not require a version bump during alpha.

Follow `AGENTS.md`. Keep agent plans, design notes, session logs, and writing-tool state outside this repository.

The collection FQCN appears throughout (`david_igou.molecule_provisioners.create`, etc.). Tooling requires it to live at `ansible_collections/david_igou/molecule_provisioners/` somewhere on `ANSIBLE_COLLECTIONS_PATH`. If working outside that layout, symlink the repo into Ansible's default search path:

```bash
mkdir -p "$HOME/.ansible/collections/ansible_collections/david_igou"
ln -snf "$PWD" "$HOME/.ansible/collections/ansible_collections/david_igou/molecule_provisioners"
```

`ansible-galaxy collection list` should then show `david_igou.molecule_provisioners` with the version declared in `galaxy.yml`, without setting `ANSIBLE_COLLECTIONS_PATH`.

## Architecture

Three top-level dispatcher playbooks (`playbooks/{create,destroy,prepare}.yml`) read `mp_backend` from the molecule group's hostvars (`hostvars[groups['molecule'][0]].mp_backend`), validate the inventory shape, and `include_role` into one of the backend roles (`roles/podman`, `roles/kubevirt`, `roles/qemu`, `roles/docker`). Each role uses `tasks_from` for lifecycle dispatch and starts with a 3-level merge (role defaults <- `mp_defaults.<backend>` <- `hostvars[item].mp.<backend>`) before looping `groups['molecule']`. Consumers' scenario `create.yml`/`destroy.yml`/`prepare.yml` are one-liners that `import_playbook: david_igou.molecule_provisioners.<phase>`. The molecule.yml itself uses molecule's ansible-native shape (`ansible:` block — no `driver:`, no `platforms:`, no `provisioner:`).

### Key files

- `playbooks/{create,destroy,prepare}.yml` — dispatcher entry points; the `import_playbook` targets that consumers reference by FQCN.
- `playbooks/reset.yml` — standalone purge playbook; removes containers labeled `owner=molecule`. Reachable as `david_igou.molecule_provisioners.reset`.
- `playbooks/group_vars/all.yml` — declares `mp_supported_backends`.
- `roles/podman/tasks/{create,destroy,prepare,_networks}.yml` — podman lifecycle. `_networks.yml` is shared between create and destroy.
- `roles/kubevirt/tasks/{create,destroy,prepare,_create_vm,_create_vm_dictionary,_build_vm,_validate}.yml` — kubevirt lifecycle. `_create_vm*.yml` are per-host helpers included in a loop over `groups['molecule']`.
- `roles/docker/tasks/{create,destroy,prepare,_spec_merge,_validate,_networks}.yml` — docker lifecycle. `_networks.yml` is shared between create and destroy.
- `roles/<backend>/defaults/main.yml` — role-level defaults including the `mp_<backend>_role_defaults` dict that feeds the merge.
- `extensions/molecule/default/` — self-test scenario carrying all four backends' specs per host. Discovered by `pytest_ansible.molecule_scenario` fixture in `tests/integration/test_integration.py`. The kubevirt-backend run talks to the cluster selected by `KUBECONFIG`, which must have KubeVirt installed. CI provisions kind + KubeVirt with `useEmulation` before running it.
- `docs/examples/` — copy-paste starter for consumers: `molecule.yml` boilerplate, `inventory/` shape, plus the deterministic-setup files (`requirements-test.yml` pinned to the Galaxy version, `config.yml` wiring it into every scenario, `ansible.cfg`, and a `MOLECULE_GLOB` `Makefile`).
- `AGENTS.md` — carries the ansible-creator agents.md reference plus a one-pass determinism checklist for agents adding a scenario in a consumer repo (pin version, centralize via `config.yml`, run from root with `MOLECULE_GLOB`, commit `ansible.cfg`).
- `docs/MIGRATION.md` — translating from molecule's pre-ansible-native `platforms:` shape to this collection.

## Do not depend on `molecule-plugins`

This collection must never list `molecule-plugins` (or any of its extras like `molecule-plugins[podman]`, `molecule-plugins[kubevirt]`) in `requirements.txt`, `test-requirements.txt`, CI install steps, or scenario `molecule.yml` `driver:` blocks. Scenarios use Molecule's ansible-native configuration and delegate the lifecycle to this collection's playbooks. If you copy a CI step from another repo and it pulls `molecule-plugins`, strip it.

## Common commands

Run Python tools in a project-local virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

| Task                                                                                | Command                                                                 |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Install runtime/test deps                                                           | `pip install -r requirements.txt -r test-requirements.txt`              |
| Lint everything                                                                     | `ansible-lint && yamllint .`                                            |
| Run podman self-test                                                                | `PROVISIONER=podman pytest tests/integration -v -k default`             |
| Run kubevirt self-test (requires `$KUBECONFIG` pointing at a cluster with KubeVirt) | `PROVISIONER=kubevirt pytest tests/integration -v -k default`           |
| Run docker self-test                                                                | `PROVISIONER=docker pytest tests/integration -v -k default`             |
| Run QEMU self-test (requires QEMU and a NoCloud seed-ISO tool)                        | `PROVISIONER=qemu pytest tests/integration -v -k default -o addopts=""`  |
| Run a single scenario directly                                                      | `cd extensions/molecule/default && PROVISIONER=<backend> molecule test` |
| Ansible sanity                                                                      | `ansible-test sanity --docker` (run from the symlink path)              |
| Build collection artifact                                                           | `ansible-galaxy collection build`                                       |
| Pre-commit                                                                          | `pre-commit run --all-files`                                            |

`pyproject.toml` configures pytest with `-n 2` (xdist parallel). The `kubevirt` CI job overrides this with `-o addopts="" -s` so molecule's `PLAY RECAP` output is visible in the runner log — xdist captures stdout per-worker, which made it impossible to tell whether the scenario was actually exercising the lifecycle.

## Public inventory schema

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
              ssh_user: <str> # optional, role default 'cloud-user' (ssh connection)
              ssh_service:
                type: NodePort # optional: NodePort (default), None, or PodIP
                port: 22 # optional in None/PodIP mode; default 22 (5986 for psrp/winrm)
              connection_ip: <str> # optional with NodePort, REQUIRED with None. Skips cluster-scoped Node lookup for this host
              # Guest connection (Windows support):
              connection: ssh # optional, 'ssh' (default) | 'psrp' | 'winrm'. psrp/winrm drop cloud-init, target 5986.
              admin_user: <str> # psrp/winrm only, default 'Administrator'
              admin_password: <str> # psrp/winrm only, REQUIRED (sensitive; no_log). Local admin the unattend set.
              sysprep_secret: <str> # optional; sysprep cdrom references sysprep.secret.name
              # Optional curated knobs:
              cpu:
                cores: <int>
                sockets: <int>
                threads: <int>
                model: <str>
              memory: <str> # role default '1Gi' → requests.memory
              memory_limit: <str> # → limits.memory
              instancetype: <str-or-dict> # string or mapping with name and kind; suppresses cpu/resources
              preference: <str-or-dict>
              node_selector: <dict>
              tolerations: <list>
              affinity: <dict>
              cloud_init: # optional; empty block keeps connection-dependent defaults
                enabled: <bool> # defaults to true for ssh, false for psrp/winrm
                inject_ssh_key: <bool> # defaults to true for ssh; false with user_data_secret
                user_data: <dict> # cloud-config mapping; management user/key merged by name
                user_data_secret: <str> # existing Secret with userdata; exclusive with user_data
                network_data: <dict> # cloud-init network-config mapping
                network_data_secret: <str> # existing Secret with networkdata; exclusive with network_data
              interfaces: <list> # replaces default interface; KubeVirt objects paired with networks by name
              networks: <list> # replaces default pod network; Multus-only requires None + connection_ip
              extra_disks: <list> # appended to [containerdisk, cloudinitdisk]
              extra_volumes: <list> # appended to [containerdisk, cloudinitdisk]
              extra_interfaces: <list> # appended after interfaces (default masquerade if omitted)
              extra_networks: <list> # appended after networks (default pod if omitted)
              vm_overrides: <dict> # escape hatch: deep-merge into whole VM, lists append
            qemu: # required when mp_backend == qemu
              image: <str> # required; upstream URL or local disk image
              cpu_model: <str> # optional; omitted/empty selects host under KVM, Nehalem under TCG (BIOS and UEFI)
              # optional: image_checksum, cpus, memory, ssh_user, firmware,
              #   disk_size, extra_args; see roles/qemu/README.md
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

`.ansible-lint` skips `var-naming[no-role-prefix]` because the collection uses an `mp_*` prefix on user-facing variables (collection-wide), which lint expects to be role-prefixed (`podman_*`, `kubevirt_*`). The `mp_*` prefix applies to all four backends.

`ansible-lint` 26.4+ requires `name:` on every play-level entry, including `import_playbook`. All scenario lifecycle one-liners and `docs/examples/` files include short imperative names.

`.yamllint` raises the line-length limit to 120 (default 80) — long URLs in `galaxy.yml` and Jinja expressions in roles routinely exceed 80.

## Pre-commit

Runs `update-docs` (collection_prep), `prettier`, `isort`, `black`, `flake8`, plus `no-commit-to-branch` against `main`. Don't bypass with `--no-verify`.

## CI

`.github/workflows/tests.yml` runs reusable changelog, build-import, ansible-lint, sanity, and unit-galaxy workflows, plus `unit-source`. Integration jobs exercise all four backends. KubeVirt runs on kind with `useEmulation`; QEMU runs under TCG and also tests CPU selection through actual guest launches. `release.yml` publishes to Galaxy on GitHub release.

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

Swap the version pin for `>=2.16,<2.17` to check ansible-core 2.16, or `>=2.15,<2.16` for the collection's stated floor.

ansible-core 2.19+ preserves Python `None` through `{{ x | default(none) }}`-style templating, but 2.16/2.17 string-coerce it to `"None"`. A `_var is not none` gate that works on the devcontainer's bleeding-edge ansible-core will silently leak content into the rendered VM on the supported floor. Run at least one cell of the matrix in a clean container before pushing a renderer change.

## Out of scope

Libvirt and cloud backends, LoadBalancer/ClusterIP+port-forward KubeVirt service types, and Molecule's `shared_state` pattern. See the [README](README.md#out-of-scope) for the full list.

Windows guests are supported on KubeVirt through PSRP or WinRM and sysprep-specialized golden images. See [Windows guests](roles/kubevirt/README.md#windows-guests-psrp--winrm).
