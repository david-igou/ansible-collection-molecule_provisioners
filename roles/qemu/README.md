# david_igou.molecule_provisioners.qemu

Runs each Molecule host as a local `qemu-system-x86_64` guest. Guests boot
from a copy-on-write overlay of a cached image, get a NoCloud cloud-init seed
with a generated management user and SSH key, and are reached over a
user-mode network SSH forward on `127.0.0.1`.

Scenarios use the collection's lifecycle playbooks; the role is not called
directly.

## Quick start

```yaml
# inventory/group_vars/molecule.yml
mp_backend: qemu
mp_defaults:
  qemu:
    image: https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
    ssh_user: debian
    memory: 2048

# inventory/hosts.yml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            qemu:
              disk_size: 20G
```

## Host fields

Set under `mp.qemu` per host or `mp_defaults.qemu` for all hosts; the host
wins. Unknown fields fail validation. Full schema:
`ansible-doc -t role david_igou.molecule_provisioners.qemu -e instance`.

| Field | Default | Description |
| --- | --- | --- |
| `image` | required | URL or local path. `.zip`, `.xz`, `.gz`, `.bz2` are unpacked; the archive must hold one image named like the archive without the extension. |
| `image_checksum` | `""` | `get_url` checksum, e.g. `sha256:<hex>` or `sha256:<url of SHA256SUMS>`. |
| `cpus` | `2` | Virtual CPUs. |
| `memory` | `1024` | MiB. |
| `ssh_user` | `cloud-user` | Created by cloud-init with the run's key and passwordless sudo. |
| `firmware` | `bios` | `bios` or `uefi` (needs OVMF). |
| `cpu_model` | `""` | `-cpu` model; empty is `host` under KVM, `Nehalem` under TCG. |
| `disk_size` | `""` | Root disk size such as `20G`; must not be smaller than the image. |
| `cloud_init.user_data` | `{}` | Cloud-config merged over the management user; lists (`users`, `runcmd`, `write_files`, ...) are appended. |
| `cloud_init.network_data` | `{}` | Cloud-init network-config; empty means DHCP. |
| `data_disks` | `[]` | `{name, size}` blank disks, in the guest at `/dev/disk/by-id/virtio-<name>`. |
| `extra_args` | `[]` | Appended verbatim to the QEMU command line. |

## Role variables

| Variable | Default | Description |
| --- | --- | --- |
| `mp_qemu_cache_dir` | `$XDG_CACHE_HOME/molecule_provisioners/qemu` | Downloaded and unpacked images. |
| `mp_qemu_download_timeout` | `60` | Download read timeout (seconds). |
| `mp_qemu_binary` | `qemu-system-x86_64` | Emulator. |
| `mp_qemu_ssh_port_base` | `20000` | Each run forwards SSH from a block of 20 ports chosen by its run id in the 10000 ports above this. |
| `mp_qemu_wait_timeout` | `300` | Prepare's wait for an Ansible connection (seconds). |
| `mp_qemu_stop_timeout` | `30` | Destroy's wait for a guest to exit (seconds). |
| `mp_qemu_ovmf_code` / `mp_qemu_ovmf_vars` | first found | UEFI firmware and variable store template. |

## How it works

- **create**: validates the merged settings, downloads the image into the
  cache (once per image), unpacks it, then writes everything for the run to
  `<molecule_ephemeral_directory>/qemu/`: an overlay qcow2, data disks, the
  seed ISO, serial log (`<host>.log`) and pidfile. The guest runs daemonized
  as `-name <host>-<run id>`. KVM is used when `/dev/kvm` is accessible.
- **prepare**: waits for an Ansible connection; on timeout the error shows the
  end of the serial log.
- **destroy**: stops each guest whose process command line still carries the
  run's name (a stale pidfile whose PID was reused is ignored), then removes the
  run directory. It succeeds when nothing was created.

## Prerequisites

On the controller:

- `qemu-system-x86_64` and `qemu-img`
- the `pycdlib` Python package (seed ISO)
- OVMF for `firmware: uefi` (`edk2-ovmf` on Fedora, `ovmf` on Debian/Ubuntu)
- collections `community.general` and `community.crypto`
- `/dev/kvm` access for usable speed; TCG works but is slow

## Notes

- **Appliance guests** without cloud-init or Python (for example MikroTik
  CHR) boot fine; the seed ISO is ignored. Ship your own `prepare.yml` that
  waits on the port and bootstraps the device, and override connection
  settings in your inventory.
- **Extra NICs and port forwards** go in `extra_args`:

  ```yaml
  extra_args:
    - -netdev
    - user,id=api,net=10.0.3.0/24,hostfwd=tcp::8728-10.0.3.15:8728
    - -device
    - virtio-net-pci,netdev=api
  ```

  Fixed host ports in `hostfwd` are shared by every run on the controller.
