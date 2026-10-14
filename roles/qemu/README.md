# `david_igou.molecule_provisioners.qemu`

Create local x86-64 guests through QEMU processes, without libvirt or a cluster.
Use the collection's `create`, `prepare`, and `destroy` dispatcher playbooks.
The controller needs Linux with pidfd support, Python 3.9+, `qemu-system-x86_64`,
`qemu-img`, `file`, and either `cloud-localds` or `genisoimage`. KVM is selected
when `/dev/kvm` is accessible; otherwise the role uses TCG.

The isolation, disk, bootstrap, network, and access features below require
`0.0.7-alpha` or later. See [#73](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/73)
for their design.

## Inventory and defaults

Define hosts in the `molecule` group. The role recursively merges its defaults,
`mp_defaults.qemu`, and each host's `mp.qemu`; lists replace earlier lists.
Only `image` is required. Defaults are two CPUs, 1024 MiB, BIOS firmware, and
`cloud-user` for bootstrap and SSH. Each merged specification is validated
before any download or resource creation. Guest and resource names contain
1–64 ASCII letters, digits, dots, underscores or hyphens, starting with a
letter or digit.

```yaml
---
all:
  children:
    molecule:
      vars:
        mp_backend: qemu
        mp_defaults:
          qemu:
            memory: 2048
            ssh_user: debian
      hosts:
        instance:
          mp:
            qemu:
              image: file:///var/lib/molecule-images/debian-cloud.qcow2
              cpus: 2
```

For remote images, supply a pinned URL and `image_checksum: algorithm:digest`.
Local images use `file:///absolute/path`. The cache ingests qcow2, raw images,
xz/gzip/bzip2 compression, and ZIP archives, converting the payload to qcow2.
ZIP ingestion selects the largest extracted file. zstd is not supported.
Cache hits are keyed by source URL, not checksum; use a new source URL when
changing a cached image. Destroy preserves cached bases.

`cpu_model` defaults to `host` under KVM and `Nehalem` under TCG for BIOS and
UEFI. Set a named supported model explicitly when a guest needs more features.
`firmware: uefi` requires OVMF code and variable template files. Writable
firmware variables are copied once and preserved across repeated create.

## Run identity and cleanup

Isolation is enabled by default. A run gets a UUID and each guest a separate
VM UUID. QEMU process names are `mp-<12-character-run-id>-<logical-host>`;
inventory host names stay unchanged. Management ports are allocated on
`127.0.0.1`, recorded, and reused until destroy. Set `ssh_host_port` to an
integer when a fixed controller port is necessary. Occupied explicit ports
fail; they are never silently replaced.

`qemu-state.json` in the ephemeral directory records ownership, process start
time, resources, and allocations. It contains no connection credentials or
cloud-init payloads. A shared controller lock and reservation file live in
`mp_qemu_runtime_cache_dir`. Every concurrent run must use the same directory.
External bind races are detected at launch and automatic allocations are
retried up to three times.

Repeated create reuses a verified guest. Changing its boot configuration
requires destroy/create. Destroy uses the manifest, so removed or reordered
inventory hosts do not prevent cleanup. It verifies PID, process start time,
executable, and QMP UUID before signaling, and uses pidfds to avoid PID reuse
while signaling. It waits for exit before removing disks or releasing ports.
Uncertain ownership or failed termination retains recovery state and reports a
failure. Restore QMP access or inspect the process before retrying; the role
does not kill an unverified process.

Keep the ephemeral directory until destroy succeeds. Never remove its state
while guests are running. Legacy guests without ownership state must be
destroyed using their original provisioner version before upgrading. See the
[migration guide](../../docs/MIGRATION.md).

## Managed storage

`disk_size` grows the root overlay at creation using an absolute size such as
`8G`; shrinking below the backing size is rejected. NoCloud enables root
partition/filesystem growth unless the caller overrides those settings.

```yaml
mp:
  qemu:
    image: file:///var/lib/molecule-images/rhel9-cloud.qcow2
    disk_size: 8G
    data_disks:
      - name: data-a
        size: 2G
        bus: virtio
        serial: molecule-data-a
      - name: data-b
        size: 2G
        bus: scsi
        serial: molecule-data-b
      - name: reference
        image: file:///var/lib/molecule-images/reference.qcow2
        bus: sata
        read_only: true
```

Disk names are unique. Blank disks require `size`; image-backed disks may omit
it or request growth. `bus` accepts `virtio` (default), `scsi`, or `sata`
(up to six SATA disks). The guest image must contain the selected bus driver;
Debian's minimal `genericcloud` kernel may omit AHCI, so use its `generic`
image for SATA tests.
Identify test disks by their optional `serial`, rather than assuming Linux
enumeration order. Guest drivers control which identifiers they expose:
CHR 7.24.5 reports a virtio data disk's serial as `vdb` instead of the supplied
QEMU serial. For that guest, verify the disk's model and capacity and exclude
the boot disk before formatting. Every managed disk is disposable; image-backed disks use
an overlay and leave the cached base unchanged. Destroy removes owned data
disks after the VM exits. Caller-owned devices supplied in `extra_args` are
outside this lifecycle.

## Cloud-init

For SSH guests, `cloud_init.enabled` and `inject_ssh_key` default to true.
Both default to false for `network_cli`. Disabling NoCloud attaches no seed
media and creates no generated bootstrap key.

```yaml
cloud_init:
  enabled: true
  inject_ssh_key: true
  mode: merge
  user_data:
    packages:
      - lvm2
    users:
      - name: auditor
        sudo: false
  metadata:
    local-hostname: storage-test
  network_data:
    version: 2
    ethernets:
      test0:
        match:
          macaddress: "52:54:00:10:00:01"
        set-name: test0
        addresses:
          - 192.0.2.10/24
```

`mode: merge` merges mappings over generated cloud-config defaults and replaces
lists. `mode: replace` uses the supplied user-data mapping. Key injection is a
separate final step: it merges the generated key by username, preserving
caller users, authorized keys, and user attributes. It gives sudo access only
to a new bootstrap user. Set `inject_ssh_key: false` for caller-managed access.

For version-two network data, the role retains management DHCP under the
`mp-mgmt` interface definition. Override that definition explicitly to change
management addressing; use the SLIRP management address `10.0.2.15` for static
configuration. Version-one network data is entirely caller-managed.

Metadata `instance-id` is reserved and stays equal to the VM UUID. Other
metadata may be customized. `network_data` is written as NoCloud
`network-config` through either ISO builder. Mapping inputs are supported;
raw user-data scripts are not. Bootstrap files and media have mode `0600`. UEFI guests attach the seed as a
read-only virtio disk so it does not depend on an IDE CD-ROM driver.
Repeated create preserves the original media and rejects changed boot inputs.
Resolved secret values belong in the caller's inventory; role tasks never
perform secret-manager lookups.

## Extra NICs and shared links

The management NIC remains `net0`. Declare additional networks and NICs:

```yaml
networks:
  - name: lan
    type: socket
nics:
  - name: test-lan
    network: lan
    model: virtio-net-pci
    mac: "52:54:00:10:00:01"
```

A `socket` network requires exactly two guest peers in this run. Declare the
same network on the other guest with a distinct MAC. The role allocates a
loopback TCP endpoint and starts the listener before the connector. Several
links can connect a router to several clients, carrying Ethernet frames for
routing, bridge, or VLAN tests. Equal network names in separate runs remain
isolated. Configure addresses and VLAN interfaces in the guests.

A `user` network creates an independent SLIRP backend for each NIC. Equal
names do not make a shared Layer-2 segment. Models default to `virtio-net-pci`;
MACs are optional; omitted MACs are derived from the VM UUID and interface
name. Supplied MACs must be unique within a run. Host
bridge/TAP creation is not managed.

`extra_args` remains a flat list of raw CLI arguments for uncommon devices or
caller-owned networks. The role reserves `-name`, `-uuid`, `-pidfile`, `-qmp`,
`-daemonize`, `-readconfig`, management and `mp-*` device IDs, and host-forward
clauses. Use `application_ports` for forwards. Disable managed serial capture
before supplying `-serial` yourself.

## Management and service access

`ssh_port` is the guest listener port (default `22`); `ssh_host_port` is the
controller port (`auto` or an integer). Runtime inventory uses the final
allocation, including any launch retry.

```yaml
mp:
  qemu:
    image: file:///var/lib/molecule-images/service-test.qcow2
    ssh_port: 2222
    cloud_init:
      enabled: false
    connection_vars:
      ansible_user: molecule
      ansible_ssh_private_key_file: /run/molecule-credentials/service-key
      ansible_python_interpreter: /usr/bin/python3
    application_ports:
      - name: api
        protocol: TCP
        port: 8080
        host_port: auto
      - name: dns
        protocol: UDP
        port: 5353
```

That image must already authorize the supplied key and run SSH on port 2222.
The generated `mp_qemu_endpoints` host variable maps each service name to its
controller `host` and `port`, guest `guest_port`, and `protocol`. Tests read
those values rather than hard-code allocated ports. `protocol` defaults to
`TCP`; `host_port` defaults to `auto`. `bind_address` defaults to `127.0.0.1`;
a specific local IPv4 address binds only that interface. Explicit `0.0.0.0`
exposes the service on all controller IPv4 interfaces.

`connection_vars` accepts `ansible_*` options, including credentials, a
caller-provided key, interpreter settings, and timeouts. It cannot override
`ansible_host`, `ansible_port`, or `ansible_connection`. Runtime inventory is
private and removed after successful cleanup.

For RouterOS or other supported appliances:

```yaml
mp:
  qemu:
    image: file:///var/lib/molecule-images/chr-test.qcow2
    cpus: 2
    connection: network_cli
    connection_probe: /system identity print
    connection_vars:
      ansible_user: molecule
      ansible_network_os: community.routeros.routeros
      ansible_ssh_private_key_file: /run/molecule-credentials/chr-key
```

Install `ansible.netcommon`, the platform collection, and their connection
requirements in the controller EE. Supply a preconfigured image. Preparation
waits for the port and executes the caller's read-only `connection_probe`
through the authenticated CLI; an open port alone is insufficient.

The tested CHR 7.24.5/QEMU 10.0.13 combination with KVM and the default
i440FX machine needs two vCPUs to detect the virtio management NIC. This is
an observed compatibility limit for that combination. Prepare CHR images to
rebind management DHCP at startup: each new VM gets a new management MAC,
and CHR can retain a DHCP binding to the template's old interface ID. Resolve
the management interface by its default name rather than the old ID, and
verify authentication on a fresh clone. Keep this bootstrap action scoped to
management; application NICs need their own configuration.

A forwarded management port can accept connections before the appliance's
SSH protocol is ready. `prepare` currently makes one authenticated CLI attempt;
if it fails during cold boot, wait for guest SSH readiness and retry prepare.

## Boot diagnostics

Serial capture and retention default to enabled. The image must configure a
serial console for it to produce boot messages. The role captures launch
stderr separately. On preparation failure it reports process/QMP status,
management endpoint, and private log paths, then fails the run.

```yaml
diagnostics:
  serial_log: true
  retain_logs: true
  failure_tail_lines: 100
```

Log files have mode `0600`. Console tails are suppressed by default because
they may contain guest secrets. Set `mp_qemu_print_diagnostic_tails: true`
explicitly to print bounded tails, limited to 1000 lines and the last 64 KiB.
Retained logs survive destroy; VM processes, disks, and reservations do not.
Set `retain_logs: false` to remove logs during destroy.

## Role options and local verification

Place role options in `inventory/group_vars/all.yml` so the localhost
create/destroy dispatchers can read them:

| Option | Default / purpose |
| --- | --- |
| `mp_qemu_run_isolation` | `true`; false selects the legacy SSH port base. Ownership verification remains enabled. |
| `mp_qemu_slirp_port_base` | `2222`; used only with isolation disabled and no explicit host port. |
| `mp_qemu_runtime_cache_dir` | `<image cache>/runtime`; shared lock and port reservations. |
| `mp_qemu_cleanup_timeout` | `30` seconds before escalating from SIGTERM to SIGKILL. |
| `mp_qemu_wait_timeout` | `180` seconds for guest authentication; increase for TCG boots. |
| `mp_qemu_ssh_key_path` | `<ephemeral>/identity_file`; generated only when injection is requested. |
| `mp_qemu_image_cache_dir` | `$XDG_CACHE_HOME/molecule-qemu`, or `~/.cache/molecule-qemu`. |
| `mp_qemu_image_download_timeout` | `60` seconds per request. |
| `mp_qemu_image_download_retries` | `5` retries, with a five-second delay. |
| `mp_qemu_ovmf_code` | `/usr/share/edk2/ovmf/OVMF_CODE.fd`. |
| `mp_qemu_ovmf_vars` | `/usr/share/edk2/ovmf/OVMF_VARS.fd`. |
| `mp_qemu_print_diagnostic_tails` | `false`; opt in to printing potentially sensitive logs. |

Run `make test-qemu-functional` for validation and paused BIOS/UEFI CPU
launches, `make test-qemu-testing` for real guest behavior, and `make test-qemu`
for the default Molecule scenario. See [local testing](../../docs/TESTING.md)
for prerequisites and backend-specific limits. Actions run lint only.
