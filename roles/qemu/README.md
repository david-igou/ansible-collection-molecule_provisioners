# `david_igou.molecule_provisioners.qemu`

Molecule provisioner role for QEMU virtual machines (process driver, SLIRP networking). Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers, which read `mp_backend` from the molecule group's hostvars.

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Computes per-host merged specs, caches the base qcow2 image (per-URL, sha256-deduped), builds a NoCloud seed ISO per host (cloud-localds or genisoimage), creates a per-VM qcow2 overlay, detects whether `/dev/kvm` is usable (falls back to TCG otherwise), and launches `qemu-system-x86_64 -daemonize` per host with a SLIRP `hostfwd` for SSH. Writes the runtime inventory with `ansible_host: 127.0.0.1` and `ansible_port: <base + host_index>`. |
| `destroy` | Terminates the per-host qemu process (PID from pidfile, verified against `/proc/<pid>/exe`), removes overlays/seed ISOs, and cleans the ephemeral connection inventory. |
| `prepare` | Waits for SSH on the forwarded port. |

## Inputs (per-host, in inventory)

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            qemu:
              image: https://cloud-images.ubuntu.com/.../noble-server.img   # required
              image_checksum: "sha256:abcdef..."                            # optional
              cpus: 2                                                        # optional, role default 2
              memory: 1024                                                   # optional MiB, role default 1024
              ssh_user: ubuntu                                               # optional, role default cloud-user
              disk_size: ""                                                  # optional; if set, growpart resizes root on first boot
```

Shared defaults can be hoisted into `mp_defaults.qemu` in `inventory/group_vars/molecule.yml` (overrides role defaults; per-host fields override mp_defaults). Field resolution order in the role: role defaults <- `mp_defaults.qemu` <- `hostvars[item].mp.qemu`.

## Role-level overrides

See `defaults/main.yml`:

- `mp_qemu_ssh_key_path` — where the per-run SSH keypair is written (default: `{{ molecule_ephemeral_directory }}/identity_file`).
- `mp_qemu_wait_timeout` — `wait_for_connection` ceiling for prepare (default `180`; TCG boots are slow).
- `mp_qemu_slirp_port_base` — base host port for SLIRP `hostfwd` (default `2222`; per-host port = base + host index).
- `mp_qemu_image_cache_dir` — base image cache root (default honours `XDG_CACHE_HOME`, else `~/.cache/molecule-qemu`).
- `mp_qemu_role_defaults` — the per-host field defaults (cpus/memory/ssh_user). Only `image` is required and is therefore absent from this dict.

## Host requirements

- `qemu-system-x86_64`, `qemu-img`
- A NoCloud seed-ISO builder: `cloud-localds` (preferred) or `genisoimage`
- `/dev/kvm` accessible to the running user (KVM acceleration); falls back to TCG otherwise
