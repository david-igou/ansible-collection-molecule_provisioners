# `david_igou.molecule_provisioners` — docker backend

**Status:** Approved
**Date:** 2026-05-22

## Problem

The collection ships podman, kubevirt, and qemu backends. Consumers on a docker-only host — Docker Desktop, rootless docker, GitHub-hosted runners that ship docker but not podman — currently have no fit. The upstream `molecule-plugins/docker` driver covers this case but the whole point of this collection is to replace those plugins (see CLAUDE.md), so we need our own docker backend.

## Solution overview

A new role `roles/docker/`, dispatched by `mp_backend: docker` via the existing top-level dispatchers. Architecturally a near-mirror of `roles/podman/` — local docker daemon, async parallel create, runtime inventory writeback, network lifecycle — using `community.docker.docker_container` + `community.docker.docker_network`, with the Ansible connection plugin set to `community.docker.docker`.

### What this design _doesn't_ ship

These are intentional omissions from the upstream `molecule-plugins/docker` driver, decided during brainstorming:

- **Wrapper-image build** (`Dockerfile.j2`, `community.docker.docker_image` build step, `pre_build_image`, `buildargs`, `cache_from`, custom `dockerfile`). Consumers ship a fully-prepared, Ansible-compatible image — same expectation as the podman role.
- **Private registry login** (`community.docker.docker_login`). Consumers either bake credentials into the docker daemon config or pre-pull the image.
- **Remote docker daemons** / TLS (`docker_host`, `cacert_path`, `cert_path`, `key_path`, `tls_verify`, `DOCKER_HOST`/`DOCKER_CERT_PATH` env fallbacks). Local socket only.

### Descoping policy

If any feature listed in the schema below turns out to be untestable inside the igou-devenv devcontainer (the development environment running this repo), drop it from the role rather than ship it unverified. The principle: a smaller verified surface beats a wider unverified one.

## Architecture

```
roles/docker/
├── defaults/main.yml           mp_docker_role_defaults + reserved networks + async timing
├── meta/main.yml               role metadata
├── meta/argument_specs.yml     per-host schema declaration
├── README.md
└── tasks/
    ├── main.yml                tasks_from dispatcher (mirrors podman)
    ├── create.yml              spec merge → validate → networks → containers (async) → runtime inventory
    ├── destroy.yml             containers absent (async) → networks absent
    ├── prepare.yml             wait_for_connection
    ├── _networks.yml           build distinct network list from per-host specs (shared by create + destroy)
    └── _validate.yml           assert image set per host
```

**Dispatcher delta**: `playbooks/group_vars/all.yml` adds `docker` to `mp_supported_backends`. The dispatcher playbooks themselves don't change — `playbooks/{create,destroy,prepare}.yml` already validate `hostvars[item].mp[_mp_backend] is defined` generically.

**Galaxy deps**: add `community.docker` to `galaxy.yml` dependencies and `requirements.txt`.

### Why module-first

Every step in the role goes through Ansible modules (`community.docker.docker_container`, `community.docker.docker_network`, `ansible.builtin.set_fact`, `ansible.builtin.copy`, `ansible.builtin.assert`, `ansible.builtin.wait_for_connection`). No shell-outs.

## Per-host schema

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            docker:
              image: <str> # required

              # container basics
              command: <str> # default omit
              command_handling: <str> # 'compatibility' | 'correct'; default 'compatibility'
              override_command: <bool> # default true; when true & command unset, force keepalive
              hostname: <str> # default = inventory_hostname (Jinja-substituted at create time, not via mp_docker_role_defaults)
              privileged: <bool> # default false
              user: <str> # default omit
              tty: <bool> # default omit
              pid_mode: <str> # default omit
              cgroupns_mode: <str> # default omit
              runtime: <str> # default omit
              platform: <str> # default omit

              # security / kernel
              capabilities: [<str>] # default []
              security_opts: [<str>] # default omit
              sysctls: { <k>: <v> } # default omit
              ulimits: [<str>] # default omit
              devices: [<str>] # default omit

              # storage
              volumes: [<str>] # default []
              mounts: [<dict>] # default omit
              tmpfs: [<str>] # default omit
              shm_size: <str> # default omit
              keep_volumes: <bool> # destroy-time; default true

              # networking
              networks: [{ name: <str>, ... }] # default omit
              network_mode: <str> # default omit
              networks_cli_compatible: <bool> # default true
              purge_networks: <bool> # default omit
              dns_servers: [<str>] # default omit
              etc_hosts: { <host>: <ip> } # default omit
              exposed_ports: [<str>] # default omit
              published_ports: [<str>] # default omit
              links: [<str>] # default omit

              # runtime behavior
              env: { <k>: <v> } # default {}
              labels: { <k>: <v> } # default {}
              restart_policy: <str> # default omit
              restart_retries: <int> # default omit
              stop_signal: <str> # default omit
              kill_signal: <str> # default omit

              # resources
              memory: <str> # default omit
              memory_swap: <str> # default omit

              # destroy
              force_kill: <bool> # destroy-time; default true
```

**Merge order** (matches every other role): `mp_docker_role_defaults` <- `mp_defaults.docker` <- `hostvars[item].mp.docker`. Only `image` is required.

**`roles/docker/defaults/main.yml`** — role-level constants:

```yaml
mp_docker_role_defaults:
  command_handling: compatibility
  override_command: true
  privileged: false
  capabilities: []
  volumes: []
  env: {}
  labels: {}
  networks_cli_compatible: true
  force_kill: true
  keep_volumes: true

mp_docker_reserved_networks:
  - bridge
  - host
  - none

mp_docker_async_timeout: 7200
mp_docker_async_retries: 300
mp_docker_async_delay: 24
```

### Schema validation

`_validate.yml` runs once at the top of `create.yml`. One assert: `_mp_specs[host].image` is set and non-empty per host. No driver/network enum validation (single-driver design).

The role's `meta/argument_specs.yml` declares the per-host schema so `ansible-lint`/`ansible-doc` can surface it, matching how podman/kubevirt/qemu declare theirs.

## Lifecycle: create

Localhost play; the dispatcher `include_role`s with `tasks_from: create`.

1. **Spec merge** — build `_mp_specs[host]` from `mp_docker_role_defaults <- mp_defaults.docker <- hostvars[item].mp.docker`.
2. **Validate** — assert `image` set per host (fail-fast, names the host).
3. **Networks (create)** — `_networks.yml` collects distinct network names from `_mp_specs[*].networks[].name`, skipping `mp_docker_reserved_networks`. Each non-reserved name → `community.docker.docker_network` `state=present`.
4. **Command directives** — compute `command_directives_dict`: for hosts where `override_command` is true and `command` is unset, default to a keepalive (`bash -c "while true; do sleep 10000; done"`). Matches molecule-plugins behavior; lets users pass plain images that lack a long-running CMD.
5. **Create containers** — `community.docker.docker_container` `state=started`, `recreate=false`, looped over `groups['molecule']`. Every option forwarded explicitly with `| default(omit)` — same convention as podman's `create.yml`. `async: mp_docker_async_timeout, poll: 0`.
6. **Wait for create** — `ansible.builtin.async_status` until each job is finished; retries=`mp_docker_async_retries`, delay=`mp_docker_async_delay`.
7. **Runtime inventory** — build `__mp_docker_runtime_hosts` mapping each host to `{ansible_connection: community.docker.docker}`. Write to `{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml`. `meta: refresh_inventory`.

`prepare.yml` runs after create via molecule's lifecycle and does a `wait_for_connection`.

## Lifecycle: destroy

`destroy.yml` is defensive — must tolerate being called against a partially-provisioned or never-created scenario.

1. **Spec merge** — same merge as create, with `default({})` on every layer so missing fields don't abort.
2. **Destroy containers** — `community.docker.docker_container` `state=absent`, `force_kill: <spec>`, `keep_volumes: <spec>`, looped, `async: mp_docker_async_timeout, poll: 0`, then `async_status`.
3. **Networks (destroy)** — `_networks.yml` recomputes the network list; each non-reserved name → `community.docker.docker_network` `state=absent`, `force: true`, with tolerance for already-absent networks.

## Error handling

- Missing `mp.docker` block on a host → caught by the top-level dispatcher's per-host check, not the role.
- Missing `image` → `_validate.yml`'s assert names the host.
- Docker daemon unreachable → `community.docker.docker_container` surfaces the daemon error naturally.
- Image pull failure → `community.docker.docker_container`'s default `pull` behavior reports the registry error; pre-pulled images work offline.
- Reserved network name in `networks[].name` → filtered before the `docker_network` call so we never try to create/destroy `bridge`/`host`/`none`.
- **Idempotency**: `recreate: false` on create; `state=absent` tolerates already-absent containers and networks.

## Testing

**Self-test scenario**: extend `extensions/molecule/default/inventory/hosts.yml` to carry a sibling `mp.docker` block on the `instance` host, alongside the existing podman/kubevirt/qemu blocks. Image and command match podman's profile to keep the converge/verify playbooks backend-agnostic:

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            podman: { image: <existing> }
            kubevirt: { image: <existing> }
            qemu: { image: <existing>, image_checksum: <existing> }
            docker:
              image: quay.io/centos/centos:stream9
              command: /sbin/init
              privileged: false
```

Switching backends from the existing matrix: `PROVISIONER=docker pytest tests/integration -v -k default`. Or directly: `cd extensions/molecule/default && PROVISIONER=docker molecule test --scenario-name default`.

`converge.yml` and `verify.yml` stay backend-agnostic (fact-gather + ping).

**CI**: new `integration-docker` job in `.github/workflows/tests.yml` modeled after `integration-podman`. GitHub-hosted runners ship docker, so the install step is `pip install -r requirements.txt -r test-requirements.txt` + `ansible-galaxy collection install community.docker`. No emulation/kind/qemu setup.

**Devcontainer-side testing**: the igou-devenv devcontainer used to develop this repo must be able to run the lifecycle end-to-end before the role lands. If it can't (no docker daemon reachable, rootless socket issues, etc.), surface that during implementation — the descoping policy above applies feature-by-feature, but a wholesale "can't run docker here" finding means we re-scope the spec before continuing.

**Fast tests**: `tests/integration/docker/test_docker_unit.py` covers spec merge, validation, destroy idempotency on never-created hosts. Plus the `-k default` molecule run for the E2E.

**Linting**: existing `ansible-lint` and `yamllint` configs pick the role up automatically.

## Documentation updates

In the same PR, also update:

- `roles/docker/README.md` — role-style README matching the others.
- `docs/examples/inventory/hosts.yml` — add `mp.docker` example block.
- `docs/examples/inventory/group_vars/molecule.yml` — add `mp_defaults.docker` example.
- `CLAUDE.md` — add docker to the "Architecture (one-paragraph version)" mention and the public-contract schema section.
- `galaxy.yml` — add `docker` to tags and `community.docker` to dependencies.
