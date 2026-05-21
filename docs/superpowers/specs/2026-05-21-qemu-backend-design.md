# `david_igou.molecule_provisioners` — qemu backend (v1.1)

**Status:** Approved (brainstorm complete, awaiting implementation plan)
**Date:** 2026-05-21
**Target release:** v1.1.0

## Problem

v1.0 ships podman and kubevirt backends. The README, MIGRATION doc, and v1.0 design all list `qemu/libvirt` as explicitly out of scope (`README.md:111`, `docs/MIGRATION.md:143`, `docs/superpowers/specs/2026-05-08-molecule-provisioners-design.md:306`). Consumers who want real VMs without a Kubernetes cluster — homelab KVM hosts, GitHub-hosted runners that just have qemu installed — have no fit.

The goal: add a `qemu` backend that covers both shapes from one role.

## Solution overview

A new role `roles/qemu/`, dispatched by `mp_backend: qemu` at the top level. Inside the role, each host's `mp.qemu.driver` selects one of two implementations:

- **`driver: libvirt`** — talks to a local `libvirtd` via `community.libvirt.virt`. URI is configurable (`qemu:///session` default; `qemu:///system` supported). All artifacts (image cache, qcow2 overlay as a libvirt volume, NoCloud seed ISO) live on the molecule controller.
- **`driver: process`** — spawns `qemu-system-x86_64 -daemonize` directly. No daemon dependency. Tracks the VM via pidfile and QMP socket in `molecule_ephemeral_directory`.

Both drivers share the same upstream concerns — image download/caching, qcow2 overlay creation, cloud-init NoCloud seed ISO, runtime inventory write-back — and diverge only at the define+start and destroy steps.

Per-host network mode (`mp.qemu.network.mode: slirp|nat`) is independent of driver, with one constraint: `nat` requires `libvirt` (no NAT path without libvirtd's `default` network in v1.1).

## Architecture

```
roles/qemu/
├── defaults/main.yml             role-level constants + mp_qemu_role_defaults
├── meta/main.yml                 role metadata
├── tasks/
│   ├── main.yml                  tasks_from dispatcher (mirrors other roles)
│   ├── create.yml                validate → merge → cache → overlay → seed → driver-dispatch → write inventory → refresh
│   ├── destroy.yml               merge → driver-dispatch destroy → cleanup
│   ├── prepare.yml               wait_for_connection (mirrors kubevirt's prepare)
│   ├── _spec_merge.yml           3-level merge: role_defaults <- mp_defaults.qemu <- hostvars[item].mp.qemu
│   ├── _image_cache.yml          get_url base qcow2 into XDG cache, keyed by sha256(url)
│   ├── _overlay.yml              libvirt: virt_volume backed by base; process: qemu-img create
│   ├── _seed_iso.yml             render NoCloud user-data + meta-data, build seed.iso
│   ├── _create_libvirt.yml       render domain XML, virt_net reserve, virt: define + state=running
│   ├── _create_process.yml       build qemu-system argv, launch with --daemonize --pidfile, record pid
│   ├── _destroy_libvirt.yml      virt: state=destroyed + undefine; virt_volume absent; virt_net: remove host; pool teardown
│   ├── _destroy_process.yml      slurp pidfile → kill -TERM → wait_for absent → file absent on artifacts
│   └── _runtime_inventory.yml    build __mp_qemu_runtime_hosts; write molecule_runtime.yml
└── templates/
    ├── domain.xml.j2             libvirt domain XML; networking branches on network.mode and KVM/TCG on /dev/kvm
    ├── user-data.j2              cloud-init NoCloud user-data (parallels the kubevirt role's inline cloud-config)
    └── meta-data.j2              cloud-init NoCloud meta-data
```

**Dispatcher delta**: `playbooks/group_vars/all.yml` adds `qemu` to `mp_supported_backends`. The dispatcher's per-host validation (`hostvars[item].mp[_mp_backend] is defined`) is already shape-correct — no changes to `playbooks/{create,destroy,prepare}.yml`.

### Why per-host driver inside one role (rejected alternatives)

- **Rejected: two sibling backends `mp_backend: libvirt` and `mp_backend: qemu`.** Cleaner code per role, but a scenario commits to a single driver per run. Per-host mixing is a stated requirement, and the libvirt vs process distinction is an implementation detail of "I want a qemu VM" — exposing it at the top level would force consumers to think about it twice (in `mp_backend` and per-host).
- **Accepted: one `qemu` backend, driver chosen per host via `mp.qemu.driver`.** Mirrors the existing pattern (`mp_backend` selects role; per-host spec selects sub-flow). Shared tasks for everything that isn't driver-specific.

### Why module-first

All libvirt interaction goes through `community.libvirt.virt`, `virt_net`, `virt_pool`, `virt_volume`. Three shell-outs remain, each unavoidable in v1.1, each called out explicitly so future module support can replace them:

1. **`qemu-img create` / `qemu-img resize`** for the process driver's overlay (no daemon → no `virt_volume`).
2. **`cloud-localds`** (or `genisoimage` fallback) for NoCloud ISO build — no Ansible module produces a NoCloud-format ISO.
3. **`kill -TERM` + `wait_for` (path absent)** for process-driver shutdown. A QMP action plugin is a candidate for v1.2 (filed as a follow-up issue rather than scoped into v1.1).

The libvirt-driver lifecycle is 100% module-driven.

## Inventory schema

**`inventory/hosts.yml` — per-host shape** (required field + optional fields shown):

```yaml
all:
  children:
    molecule:
      hosts:
        ubuntu-24:
          mp:
            qemu:
              image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
              image_checksum: "sha256:abcd…"     # forwarded to get_url; optional
              driver: libvirt                    # libvirt | process; defaults to libvirt
              uri: qemu:///session               # libvirt driver only
              cpus: 2
              memory: 1024                       # MiB, integer (matches qemu's -m argument)
              disk_size: 10G                     # passed to qemu-img resize; null = no resize
              ssh_user: ubuntu
              network:
                mode: slirp                      # slirp | nat
              extra_args: []                     # process driver only; appended to qemu-system argv
```

**`inventory/group_vars/molecule.yml` — backend selector + defaults**:

```yaml
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

mp_defaults:
  qemu:
    driver: libvirt
    uri: qemu:///session
    cpus: 2
    memory: 1024
    ssh_user: cloud-user
    network:
      mode: slirp
```

**`roles/qemu/defaults/main.yml` — role-level constants**:

```yaml
mp_qemu_role_defaults:
  driver: libvirt
  uri: qemu:///session
  cpus: 2
  memory: 1024
  ssh_user: cloud-user
  network:
    mode: slirp

mp_qemu_image_cache_dir: "{{ lookup('env', 'XDG_CACHE_HOME') | default(ansible_env.HOME ~ '/.cache', true) }}/molecule-qemu"
mp_qemu_ssh_key_path: "{{ molecule_ephemeral_directory }}/identity_file"
mp_qemu_wait_timeout: 180
mp_qemu_slirp_port_base: 2222
mp_qemu_allowed_drivers: [libvirt, process]
mp_qemu_allowed_network_modes: [slirp, nat]
```

The merge order matches kubevirt's: `mp_qemu_role_defaults <- mp_defaults.qemu <- hostvars[item].mp.qemu`. Only `image` is required and is therefore absent from `mp_qemu_role_defaults`.

### Schema validation

Asserted at the top of `create.yml`, one block, fail-fast (mirrors kubevirt's `ssh_service.type` assert):

1. `_mp_specs[host].driver in mp_qemu_allowed_drivers`
2. `_mp_specs[host].network.mode in mp_qemu_allowed_network_modes`
3. `network.mode == 'nat'` requires `driver == 'libvirt'`
4. `_mp_specs[host].image` is set and non-empty
5. `mp_qemu_image_cache_dir` exists and is writable (`file: state=directory`, then `stat` check)

### Schema notes

- **`memory` is integer MiB, not `"1Gi"`.** Matches qemu-system's `-m` argument directly. Slightly inconsistent with kubevirt's K8s-idiom `"1Gi"`, but each backend uses its own idiom.
- **`disk_size` is optional.** If set, the role runs `qemu-img resize` after creating the overlay; cloud-init's `growpart` + `resizefs` expand the root filesystem at first boot. If unset, the VM is limited to the base image's size (~2.2 GiB for Ubuntu cloud images) — fine for tiny molecule tests.

## Lifecycle: create

Localhost play; all action delegated through `include_role: tasks_from: create`.

1. **Preflight** — assert `qemu-system-x86_64` on PATH (`command --version, changed_when: false`). For driver=libvirt hosts, assert `libvirtd` reachable per unique URI via `community.libvirt.virt: command=list_vms`. Both fail at validate-time, not deep in a per-host loop.
2. **Spec merge** (`_spec_merge.yml`) — builds `_mp_specs[host]` from the three layers above. Same `combine` chain as kubevirt's `_create_vm_dictionary.yml`.
3. **Validate** — the five assertions from the schema section.
4. **SSH keypair** — `community.crypto.openssh_keypair` at `mp_qemu_ssh_key_path` (ephemeral dir). Mirrors kubevirt. `temporary_ssh_public_key` fact set for templates.
5. **Image cache** (`_image_cache.yml`, loop per host) — for each unique `image` URL: cache subdir is `{{ mp_qemu_image_cache_dir }}/{{ image | ansible.builtin.hash('sha256') }}/`; `get_url` with optional `checksum`. Idempotent — re-runs no-op when checksum matches.
6. **Overlay create** (`_overlay.yml`, loop per host) —
   - **libvirt path**: ensure a transient `dir`-type storage pool via `community.libvirt.virt_pool` rooted at `molecule_ephemeral_directory`; then `community.libvirt.virt_volume` with `xml:` to create a qcow2 volume backed by the cached base. The domain XML references the volume by pool+vol name.
   - **process path**: `ansible.builtin.command: qemu-img create -f qcow2 -F qcow2 -b <base> <overlay>` with `creates:`.
   - Both paths follow up with `qemu-img resize` if `disk_size` is set (no module exists for resize).
7. **Seed ISO** (`_seed_iso.yml`, loop per host) — render `user-data.j2` and `meta-data.j2` into `{{ molecule_ephemeral_directory }}/{{ host }}-cidata/`. Detect `cloud-localds` via `command: which cloud-localds, failed_when: false`; if present use it, else fall back to `genisoimage -volid cidata -joliet -rock`.
8. **NAT pre-reservation** (libvirt + nat hosts only) — read libvirt's `default` network XML via `community.libvirt.virt_net: command=get_xml`, parse the IPv4 subnet and DHCP range from the returned XML, then for each NAT host:
   - Generate a deterministic MAC: `52:54:00:` + 3 bytes from `sha256('{{ molecule_ephemeral_directory }}-{{ host }}')` (ephemeral dir is unique per scenario run, so MACs don't collide across concurrent scenarios).
   - Pick an IP outside the DHCP range, offset by host index within `groups['molecule']`, falling at the high end of the subnet.
   - Inject a `<host mac='…' ip='…' name='{{ host }}'/>` entry via `community.libvirt.virt_net: command=modify, name=default`.

   The role then *knows* the IP — no DHCP-lease polling.
9. **Driver dispatch** — split hosts by `_mp_specs[host].driver` and `include_tasks` the appropriate `_create_<driver>.yml`:
   - **`_create_libvirt.yml`**: render `domain.xml.j2`; `community.libvirt.virt: command=define`; `community.libvirt.virt: name={{ host }} state=running`. Domain XML uses `<domain type='kvm'>` if `/dev/kvm` is readable+writable (detected via `slurp` perms once at the top of create), else `<domain type='qemu'>` (TCG). Networking: SLIRP → `<interface type='user'>` with `<portForward>`; NAT → `<interface type='network'><source network='default'/>` with the pinned MAC.
   - **`_create_process.yml`**: build argv (`-machine accel=kvm:tcg`, `-cpu host` only under KVM, `-m`, `-smp`, `-drive file=<overlay>,if=virtio`, `-cdrom <seed.iso>`, `-netdev user,id=net0,hostfwd=tcp::<port>-:22`, `-device virtio-net-pci,netdev=net0`, `-daemonize`, `-pidfile <ephemeral>/{{ host }}.pid`, `-qmp unix:<ephemeral>/{{ host }}.qmp,server,nowait`, plus any `_mp_specs[host].extra_args`); launch via `ansible.builtin.command`.
10. **Runtime inventory** (`_runtime_inventory.yml`, loop per host) — build `__mp_qemu_runtime_hosts[host]` with:
    - **SLIRP** (both drivers): `ansible_host: 127.0.0.1`, `ansible_port: <mp_qemu_slirp_port_base + index_in_groups.molecule>`, `ansible_user: <ssh_user>`, `ansible_ssh_private_key_file: <key_path>`, `ansible_connection: ssh`, `ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'`.
    - **NAT** (libvirt only): `ansible_host: <reserved IP from step 8>`, `ansible_port: 22`, same auth.
    - Write `{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml`; `meta: refresh_inventory`.
11. **`prepare.yml`** runs after create via the molecule lifecycle — `wait_for_connection: timeout={{ mp_qemu_wait_timeout }}`. Identical shape to kubevirt's prepare.

### Port assignment

SLIRP host ports are `mp_qemu_slirp_port_base + index_of_host_in_groups.molecule`. Deterministic, predictable, no QMP round-trip. Risk: collision with another molecule scenario already running on the same controller. Mitigation: per-host override — if `_mp_specs[host].network.host_port` is set, the role uses it instead of the index-derived value. Documented in the schema but not exposed by default.

## Lifecycle: destroy

`destroy.yml` is defensive by design — it tolerates being called against a partially-provisioned scenario (e.g., create failed midway).

1. **Spec merge** — same merge as create, with `default({})` on every layer so missing fields don't abort the destroy.
2. **Driver dispatch** — split hosts by driver:
   - **`_destroy_libvirt.yml`**: `community.libvirt.virt: state=destroyed` (idempotent if already stopped), `command=undefine` (idempotent if missing); `community.libvirt.virt_volume: state=absent`; `community.libvirt.virt_net: command=modify` to remove the `<host>` entry (matched by MAC); `file: state=absent` on overlay path (belt-and-suspenders for volume remove).
   - **`_destroy_process.yml`**: `slurp` pidfile (`failed_when: false`); if read succeeded and `/proc/<pid>/exe` confirms `qemu-system`, `command: kill -TERM <pid>`; `wait_for: path={{ pidfile }} state=absent timeout=30`; if still present, `kill -KILL`; `file: state=absent` on overlay, seed ISO, pidfile, QMP socket.
3. **Pool teardown** (libvirt path) — once all hosts using a given pool are destroyed, the role removes the transient pool: `community.libvirt.virt_pool: command=destroy` then `command=undefine`. The role created it, the role owns it.

Every `state=absent` step has `failed_when: false` where the missing-resource case is expected (e.g., destroying a scenario that never got past validation).

## Error handling

**Validation failures** (fail-fast at the top of create):
- Missing `mp.qemu` block → already handled by the dispatcher (`playbooks/create.yml`).
- Bad `driver` / `network.mode` → asserted with the allowed-values list; fail message names the host and the offending value.
- `process + nat` combination → explicit fail message: "process driver supports slirp only in v1.1."
- Image URL unreachable / checksum mismatch → `get_url` surfaces this naturally.
- `qemu-system-x86_64` missing → preflight `command` returns 127.
- `libvirtd` unreachable (driver=libvirt) → preflight `virt: command=list_vms` errors with the URI.

**Mid-run failures**:
- libvirt define succeeds but start fails → `virt: state=destroyed`+`undefine` in destroy handles both states idempotently.
- Process launch succeeds but VM hangs at boot → `wait_for_connection` in prepare times out; destroy still finds the pidfile.
- Stale pidfile (controller killed mid-run) → destroy confirms the pid still maps to `qemu-system` via `/proc/<pid>/exe` slurp before signaling.
- Stale libvirt `<host>` reservation from a prior run → `virt_net: command=modify` overwrites by MAC on create; destroy removes by MAC.

**KVM detection**: `slurp` `/dev/kvm` permissions once at the top of create. If readable+writable → KVM. Else → TCG, no warning. A `/dev/kvm` present but permission-locked falls back silently; this is the stated behavior, not an error.

**Idempotency**: every file-producing step uses `creates:`; every libvirt module step is naturally idempotent; image cache is keyed by URL hash so two scenarios pulling the same image share the cache.

## Testing

**Self-test scenario**: `extensions/molecule/qemu/`, mirroring the existing `extensions/molecule/default/` shape but with `mp_backend: qemu` and three hosts exercising all valid driver × network combinations:

```yaml
all:
  children:
    molecule:
      hosts:
        ubuntu-libvirt-slirp:
          mp: { qemu: { image: <ubuntu-noble-url>, image_checksum: <pinned>, driver: libvirt, network: { mode: slirp } } }
        ubuntu-process-slirp:
          mp: { qemu: { image: <ubuntu-noble-url>, image_checksum: <pinned>, driver: process, network: { mode: slirp } } }
        ubuntu-libvirt-nat:
          mp: { qemu: { image: <ubuntu-noble-url>, image_checksum: <pinned>, driver: libvirt, network: { mode: nat } } }
```

(`process + nat` is rejected by validation, so 3 of 4 combos cover all valid ones.)

**NAT is a CI-only merge gate.** Many local dev environments (including the maintainer's CentOS-based devcontainer) can't bring up libvirt's `default` network — the `virbr0` bridge either isn't permitted by the host namespace or `dnsmasq` collides with `192.168.122.1` already held by a sibling network. The self-test scenario keeps the `ubuntu-libvirt-nat` host so CI exercises the NAT pre-reservation + `<host>` injection path end-to-end, but local merge sign-off only requires the two SLIRP hosts (`ubuntu-libvirt-slirp`, `ubuntu-process-slirp`) to pass. Running `molecule test` locally on an environment without a healthy `default` network is expected to fail on the NAT host; this is not a blocker for merge.

**`converge.yml`**: `command: uname -a`, `assert: ansible_distribution == 'Ubuntu'`.
**`verify.yml`**: assert each host's `ansible_host`/`ansible_port` matches the expected mode — `127.0.0.1` + assigned port for SLIRP, libvirt-default-subnet IP + 22 for NAT.

**CI job** (new GitHub Actions workflow, mirrors the kubevirt job's approach from `docs/superpowers/specs/2026-05-08-kubevirt-ci-design.md`):
- Runs on `ubuntu-latest`. Installs `qemu-system-x86_64`, `qemu-utils`, `libvirt-daemon-system`, `libvirt-clients`, `cloud-image-utils`, `bridge-utils`. Starts `libvirtd`, confirms `qemu:///system` reachable.
- `/dev/kvm` is **not** available on GitHub-hosted runners — the job exercises the TCG branch. The KVM branch is exercised on the maintainer's homelab.
- `actions/cache@v4` keyed on the Ubuntu cloud image's pinned sha256 maps to `~/.cache/molecule-qemu/`; subsequent runs skip the ~600 MB download.
- Job timeout 15 min; `mp_qemu_wait_timeout` override to 300 s via group_vars for the CI scenario.

**Unit tests**: none. The role is templates + module calls; integration tests cover it (same approach as podman and kubevirt).

**Linting**: existing `ansible-lint` and `yamllint` configs pick the role up automatically.

## Documentation

**`README.md` updates**:
- Add `qemu` row to "Supported backends" table.
- Add a `qemu:` block to the inventory example, parallel to `podman:` and `kubevirt:`.
- Remove `qemu/libvirt` from the "Out of scope" list.
- Document controller-host prereqs: `qemu-system-x86_64`, `qemu-img`, `cloud-localds` (or `genisoimage`), and — for the libvirt driver — `libvirtd` reachable at the URI.

**`docs/MIGRATION.md` updates**:
- New subsection "Migrating from `molecule-plugins[libvirt]`" mapping the libvirt `platforms[]` shape (`vcpus`, `memory`, `libvirt_user`, etc.) to `mp.qemu.<field>`.
- Remove `qemu/libvirt` from the "What this collection does NOT support" list.

## Versioning

v1.1.0 — additive only.

- New backend, new optional schema keys → minor bump per the v1.0 versioning contract (design.md §"Versioning").
- No breaking changes to podman, kubevirt, or `mp_backend` dispatch.
- `galaxy.yml` adds `community.libvirt: ">=1.3.0"` to deps.

## Out of scope for v1.1

Deferred to future minor versions:

- **`network.mode: bridge`** — needs root, bridge config on the controller, and a more elaborate prereq story. Schema is shaped for additive extension (`mode:` enum).
- **Remote libvirt URIs** (`qemu+ssh://kvm-host/system`). All artifacts assumed controller-local in v1.1. Adding remote support is `delegate_to` plumbing on the cache/overlay/seed steps — additive.
- **QMP action plugin for graceful shutdown.** v1.1 uses `kill -TERM` + `wait_for`; filed as a follow-up issue.
- **Per-host static IP override in slirp mode** beyond the `host_port` override.
- **Non-cloud-init images** (custom kickstart, plain raw disks). Cloud-init is assumed.
- **Windows / non-Linux guests.**
