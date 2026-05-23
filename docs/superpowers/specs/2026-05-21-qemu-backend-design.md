# `david_igou.molecule_provisioners` — qemu backend (v1.1)

**Status:** Approved (scaled down from the original libvirt+process design after local libvirt-driver verification surfaced multiple interacting bugs not worth fixing in v1.1)
**Date:** 2026-05-21
**Target release:** v1.1.0

## Problem

v1.0 ships podman and kubevirt backends. Consumers who want real VMs without a Kubernetes cluster — homelab KVM hosts, GitHub-hosted runners that just have qemu installed — have no fit.

The goal: add a `qemu` backend that gives consumers a real VM via the smallest possible host-prereq surface.

## Solution overview

A new role `roles/qemu/`, dispatched by `mp_backend: qemu` at the top level. The role spawns `qemu-system-x86_64 -daemonize` directly — no `libvirtd` dependency, no `community.libvirt` collection dependency. SSH reaches the VM via SLIRP user-mode networking with a host-side port forward (`-netdev user,id=net0,hostfwd=tcp::<port>-:22`).

Shared concerns — base-image download + cache, qcow2 overlay, NoCloud cloud-init seed ISO, runtime-inventory write-back — sit alongside the process-launch step. No driver dispatch.

### What this design _doesn't_ ship in v1.1

- **libvirt driver.** The original v1.1 design included a `mp.qemu.driver: libvirt|process` switch. During local verification we surfaced four interacting bugs (`community.libvirt.virt_volume` `command: create` dispatch, hardcoded `<emulator>` path, libvirt 11.x's mandatory `<backend type='passt'/>` for `<portForward>`, and devcontainer-level passt-sandbox `/proc` access) that together made the libvirt path more cost than value for v1.1. Removed entirely; can come back in a future minor if needed.
- **NAT networking.** Was tied to the libvirt driver (libvirt's `default` network + `<host>` reservation). Gone with libvirt.
- **Remote URIs, bridge networking, Windows guests, non-cloud-init images.** Same as before — not in v1.1.

## Architecture

```
roles/qemu/
├── defaults/main.yml             mp_qemu_role_defaults + image cache dir + SLIRP port base
├── meta/main.yml                 role metadata
├── tasks/
│   ├── main.yml                  tasks_from dispatcher (mirrors other roles)
│   ├── create.yml                merge → validate → cache → seed → overlay → KVM detect → launch → write inventory
│   ├── destroy.yml               merge → process destroy (per host) → cidata cleanup
│   ├── prepare.yml               wait_for_connection (mirrors kubevirt's prepare)
│   ├── _spec_merge.yml           3-level merge: role_defaults <- mp_defaults.qemu <- hostvars[item].mp.qemu
│   ├── _validate.yml             one assertion: `image` is set non-empty per host
│   ├── _image_cache.yml          get_url base qcow2 into XDG cache, keyed by sha256(url)
│   ├── _overlay.yml              qemu-img create -f qcow2 -b <base>; optional qemu-img resize
│   ├── _seed_iso.yml             render NoCloud user-data + meta-data, build seed.iso via cloud-localds | genisoimage
│   ├── _seed_iso_host.yml        per-host helper for _seed_iso.yml
│   ├── _create_process.yml       build qemu-system argv, launch with --daemonize --pidfile, record pid + ssh facts
│   ├── _destroy_process.yml      slurp pidfile → kill -TERM → wait_for absent → file absent on artifacts
│   └── _runtime_inventory.yml    build __mp_qemu_runtime_hosts; write molecule_runtime.yml
└── templates/
    ├── user-data.j2              cloud-init NoCloud user-data
    └── meta-data.j2              cloud-init NoCloud meta-data
```

**Dispatcher delta**: `playbooks/group_vars/all.yml` adds `qemu` to `mp_supported_backends`. The dispatcher's per-host validation (`hostvars[item].mp[_mp_backend] is defined`) is already shape-correct — no changes to `playbooks/{create,destroy,prepare}.yml`.

### Why module-first

Almost all role steps go through Ansible modules: `ansible.builtin.get_url`, `ansible.builtin.file`, `ansible.builtin.template`, `ansible.builtin.set_fact`, `community.crypto.openssh_keypair`, `ansible.builtin.wait_for`. Three shell-outs remain, each unavoidable in v1.1:

1. **`qemu-img create` / `qemu-img resize`** for the overlay (no first-class Ansible module for qcow2 backing files).
2. **`cloud-localds`** (or `genisoimage` fallback) for NoCloud ISO build (no Ansible module produces a NoCloud-format ISO).
3. **`kill -TERM` + `wait_for` (path absent)** for shutdown. A QMP action plugin is a candidate for a later minor; filed as a follow-up.

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
              image_checksum: "sha256:abcd…" # forwarded to get_url; optional
              cpus: 2 # default 2
              memory: 1024 # MiB, integer; default 1024
              disk_size: 10G # passed to qemu-img resize; null = no resize
              ssh_user: ubuntu # default 'cloud-user'
              extra_args: [] # appended to qemu-system argv
```

**`inventory/group_vars/molecule.yml` — backend selector + defaults**:

```yaml
mp_backend: "{{ lookup('env', 'PROVISIONER') | default('podman', true) }}"

mp_defaults:
  qemu:
    cpus: 2
    memory: 1024
    ssh_user: cloud-user
```

**`roles/qemu/defaults/main.yml` — role-level constants**:

```yaml
mp_qemu_role_defaults:
  cpus: 2
  memory: 1024
  ssh_user: cloud-user

mp_qemu_image_cache_dir: "{{ (lookup('env', 'XDG_CACHE_HOME')
  | default(lookup('env', 'HOME') ~ '/.cache', true))
  ~ '/molecule-qemu' }}"
mp_qemu_ssh_key_path: "{{ molecule_ephemeral_directory }}/identity_file"
mp_qemu_wait_timeout: 180
mp_qemu_slirp_port_base: 2222
```

The merge order matches kubevirt's: `mp_qemu_role_defaults <- mp_defaults.qemu <- hostvars[item].mp.qemu`. Only `image` is required and is therefore absent from `mp_qemu_role_defaults`.

### Schema validation

Asserted at the top of `create.yml`, fail-fast: `_mp_specs[host].image` is set and non-empty. Image-cache-dir writability is enforced by the subsequent `file: state=directory mode=0755` step. No driver/network enums to validate (single-driver, single-network design).

## Lifecycle: create

Localhost play; all action delegated through `include_role: tasks_from: create`.

1. **Spec merge** — builds `_mp_specs[host]` from the three layers above.
2. **Validate** — assert `image` is set per host.
3. **Image cache** — `get_url` each unique image URL into `{{ mp_qemu_image_cache_dir }}/{{ image | hash('sha256') }}/disk.qcow2`. Idempotent.
4. **Seed ISO** — render `user-data.j2` + `meta-data.j2`; build NoCloud ISO via `cloud-localds` if present, else `genisoimage -volid cidata -joliet -rock`.
5. **Overlay** — `qemu-img create -f qcow2 -F qcow2 -b <cached base> <ephemeral>/<host>.qcow2`. Optional `qemu-img resize` if `disk_size` set.
6. **KVM detection** — `slurp /dev/kvm` perms once; set `_mp_qemu_kvm_ok` fact. Consumed by `_create_process.yml` to choose `-machine accel=kvm:tcg` vs `accel=tcg`.
7. **Launch** — build qemu-system-x86_64 argv (`-machine accel=...`, `-m`, `-smp`, `-drive file=<overlay>,if=virtio,format=qcow2`, `-cdrom <seed.iso>`, `-netdev user,id=net0,hostfwd=tcp::<port>-:22`, `-device virtio-net-pci,netdev=net0`, `-daemonize`, `-pidfile <ephemeral>/<host>.pid`, `-qmp unix:<ephemeral>/<host>.qmp,server,nowait`, `-display none`, plus `extra_args`). Launch via `ansible.builtin.command` with `creates: <pidfile>`.
8. **Runtime inventory** — for each host, write `ansible_host: 127.0.0.1`, `ansible_port: <base + host index>`, `ansible_user: <ssh_user>`, `ansible_ssh_private_key_file: <key_path>`, `ansible_connection: ssh`, `ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'`. Write to `{{ molecule_ephemeral_directory }}/inventory/molecule_runtime.yml`; `meta: refresh_inventory`.
9. **`prepare.yml`** runs after create via the molecule lifecycle — `wait_for_connection: timeout={{ mp_qemu_wait_timeout }}`.

### Port assignment

SLIRP host ports are `mp_qemu_slirp_port_base + index_of_host_in_groups.molecule`. Deterministic. Two molecule scenarios on the same controller share the same port-base range — concurrent scenarios on one controller need a per-scenario `mp_qemu_slirp_port_base` override.

## Lifecycle: destroy

`destroy.yml` is defensive — it tolerates being called against a partially-provisioned scenario.

1. **Spec merge** — same merge as create, with `default({})` on every layer so missing fields don't abort.
2. **Per-host process destroy** — for each `groups['molecule']`: `slurp` the pidfile (`failed_when: false`); if it parsed and `/proc/<pid>/exe` resolves to `qemu-system`, `kill -TERM <pid>`; `wait_for` pidfile absent (30s); if still present, `kill -KILL`; `file: state=absent` on `<host>.qcow2`, `<host>.pid`, `<host>.qmp`, `<host>-seed.iso`.
3. **cidata cleanup** — `file: state=absent` on `<host>-cidata/` directories.

Every `state=absent` step has `failed_when: false` where the missing-resource case is expected.

## Error handling

- Missing `mp.qemu` block → handled by the dispatcher (`playbooks/create.yml`).
- Missing `image` → `_validate.yml`'s assert names the host.
- Image URL unreachable / checksum mismatch → `get_url` surfaces this naturally.
- `qemu-system-x86_64` missing → `_create_process.yml`'s `command` returns 127; surfaces with a clear errno.
- VM hangs at boot → `wait_for_connection` in prepare times out at `mp_qemu_wait_timeout`; destroy still finds the pidfile.
- Stale pidfile (controller killed mid-run) → destroy confirms the pid still maps to `qemu-system` via `/proc/<pid>/exe` slurp before signaling.
- **KVM detection**: `slurp` `/dev/kvm` once. Readable+writable → KVM. Else → TCG, no warning. Permission-locked falls back silently.

**Idempotency**: every file-producing step uses `creates:`; image cache is keyed by URL hash so two scenarios pulling the same image share the cache.

## Testing

**Self-test scenario**: `extensions/molecule/default/` — the same single scenario used by podman and kubevirt. The `instance` host carries three sibling backend blocks (`mp.podman`, `mp.kubevirt`, `mp.qemu`); the active backend is picked by `PROVISIONER` at run time:

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            podman: { image: <fedora-podman-image> }
            kubevirt: { image: <fedora-containerdisk> }
            qemu:
              image: <ubuntu-noble-cloud-image-url>
              image_checksum: <pinned-sha256>
```

Switching backends: `PROVISIONER=podman pytest tests/integration -v -k default`, `PROVISIONER=kubevirt …`, `PROVISIONER=qemu …`. Or directly: `cd extensions/molecule/default && PROVISIONER=qemu molecule test --scenario-name default`.

**`converge.yml`**: gathers facts and debug-prints `ansible_hostname`/`ansible_distribution`. Backend-agnostic.
**`verify.yml`**: `ansible.builtin.ping` each molecule host.

**CI job** (`.github/workflows/tests.yml` → `integration-qemu`):

- Runs on `ubuntu-latest`. Installs `qemu-system-x86_64`, `qemu-utils`, `cloud-image-utils`.
- `/dev/kvm` is **not** available on GitHub-hosted runners — the job exercises the TCG branch.
- `actions/cache@v4` keyed on the Ubuntu cloud image's pinned sha256 maps to `~/.cache/molecule-qemu/`; subsequent runs skip the ~600 MB download.
- Job timeout 30 min. Selector is `-k default` (same as the podman + kubevirt CI jobs), with `PROVISIONER=qemu` picking the backend. `mp_qemu_wait_timeout: 300` override lives in the scenario's `group_vars/molecule.yml`.

**Fast tests**: `tests/integration/qemu/test_qemu_unit.py` covers spec merge, validation, image cache (via `file://` URL), seed ISO build, destroy idempotency on never-created hosts. Plus a `@pytest.mark.slow` E2E that boots the VM under TCG and asserts pidfile + runtime inventory presence.

**Linting**: existing `ansible-lint` and `yamllint` configs pick the role up automatically.

## Versioning

v1.1.0 — additive only.

- New backend, new optional schema keys → minor bump per the v1.0 versioning contract (design.md §"Versioning").
- No breaking changes to podman, kubevirt, or `mp_backend` dispatch.
- No new galaxy dependencies vs. v1.0.

## Out of scope for v1.1

Deferred to future minor versions:

- **libvirt driver.** Future minor; needs upstream `community.libvirt` module fixes and devcontainer-friendly passt sandboxing before it's worth re-attempting.
- **NAT / bridge networking.** SLIRP only in v1.1.
- **Remote libvirt URIs** (`qemu+ssh://...`).
- **QMP action plugin for graceful shutdown.** v1.1 uses `kill -TERM` + `wait_for`.
- **Non-cloud-init images.** Cloud-init is assumed.
- **Windows / non-Linux guests.**
