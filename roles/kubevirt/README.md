# kubevirt

Molecule backend that runs each `molecule` host as a KubeVirt
VirtualMachine. Use it through the collection playbooks; do not call the role
directly.

```yaml
# create.yml (likewise destroy.yml and prepare.yml)
---
- name: Create the instances
  ansible.builtin.import_playbook: david_igou.molecule_provisioners.create
```

## Requirements

- A cluster with KubeVirt, selected by `KUBECONFIG` (or the usual
  `K8S_AUTH_*` variables). CDI is needed for `data_volume` boot sources and
  `data_disks`.
- On the controller: the Python `kubernetes` library and the
  `kubernetes.core` and `community.crypto` collections. Windows guests also
  need `pypsrp` (psrp) or `pywinrm` (winrm), and `ansible.windows` for the
  readiness check in prepare.
- The controller must reach the guest: a node's InternalIP for `NodePort`, the
  pod network for `PodIP`, or `connection_ip` for `None`.

## Quick start

```yaml
# inventory/group_vars/molecule.yml
mp_backend: kubevirt
mp_defaults:
  kubevirt:
    namespace: molecule
    boot_source:
      type: container_disk
      image: quay.io/containerdisks/ubuntu:24.04
    ssh_user: ubuntu
    memory: 1Gi
```

```yaml
# inventory/hosts.yml
all:
  children:
    molecule:
      hosts:
        instance-1: {}
        instance-2:
          mp:
            kubevirt:
              memory: 2Gi
```

## How it works

Each host's settings are `mp_kubevirt_instance_defaults`, then
`mp_defaults.kubevirt`, then the host's `mp.kubevirt`. They are merged
recursively in that order, so a host overrides single keys of a dict. Lists
replace. The merged settings are validated against the `instance` entry point
of the argument spec, and unknown keys fail:

```bash
ansible-doc -t role david_igou.molecule_provisioners.kubevirt -e instance
```

`create` then:

1. generates an ed25519 key at `$MOLECULE_EPHEMERAL_DIRECTORY/identity_file`
2. stores the cloud-init data in a Secret
3. renders the VirtualMachine from `templates/vm.yml.j2`, merges
   `vm_overrides` over it, applies it and waits for the `Ready` condition
4. for `NodePort`, applies a Service (server-side, so allocated NodePorts
   survive a rerun)
5. records the connection settings, which the collection writes to the
   Molecule runtime inventory

Every resource is named `<inventory_hostname>-<mp_run_id>` and carries the
labels `app.kubernetes.io/managed-by: molecule_provisioners`,
`molecule-provisioners/run-id` and `molecule-provisioners/instance`. Parallel
runs in one namespace never collide. `destroy` deletes the VirtualMachine (in
the foreground, so its DataVolumes and PVCs go first), Service and Secret
selected by the instance label. It succeeds when nothing exists.

`prepare` waits for the guest connection and, for cloud-init guests, for
`cloud-init status --wait`.

## Host settings

| Key | Default | Description |
| --- | --- | --- |
| `boot_source` | required | `type` plus the fields for that type; see below. |
| `namespace` | `molecule` | Namespace for all of the host's resources. |
| `ssh_user` | `cloud-user` | Management user created by cloud-init with the generated key. |
| `memory` | `1Gi` | Guest memory request, such as `2Gi`. Not used with `instancetype`. |
| `cpu` | | `cores`, `sockets`, `threads`, `model`. KubeVirt's default is one vCPU. |
| `instancetype` | | Cluster instancetype name. Excludes `cpu` and `memory`. |
| `preference` | | Cluster preference name, such as `windows.11`. |
| `node_selector`, `tolerations`, `affinity` | empty | Pod scheduling. |
| `cloud_init` | `{}` | `enabled`, `user_data`, `network_data`; see below. |
| `interfaces`, `networks` | one masquerade interface on the pod network, both named `default` | Replace the defaults. Paired by name. |
| `boot_disk` | `{}` | `bus` (`virtio`, `sata`, `scsi`), `boot_order`. |
| `data_disks` | `[]` | Extra DataVolume disks; see below. |
| `connection` | `ssh` | `ssh`, `psrp` or `winrm`. |
| `connection_vars` | `{}` | Host vars merged over the generated connection settings. |
| `connection_ip` | | Address to connect to. Required with `ssh_service.type: None`. |
| `ssh_service` | `{type: NodePort}` | `type` (`NodePort`, `PodIP`, `None`) and the guest `port` (22, or 5986 for Windows). |
| `application_ports` | `[]` | `name`, `port`, `target_port`, `protocol` (TCP/UDP). |
| `sysprep_secret` | | Existing Secret with `unattend.xml`, attached as a sysprep CD-ROM. |
| `vm_overrides` | `{}` | Merged over the rendered VirtualMachine; lists append. |

Role variables:

| Variable | Default | Description |
| --- | --- | --- |
| `mp_kubevirt_instance_defaults` | see `defaults/main.yml` | First layer of every host's settings. |
| `mp_kubevirt_wait_timeout` | `600` | Seconds for the VM to become Ready (disk imports included), for the pod IP, for deletion and for an SSH guest in prepare. |
| `mp_kubevirt_windows_wait_timeout` | `900` | Seconds prepare waits for a psrp or winrm guest. |

### Boot sources

```yaml
boot_source:            # an ephemeral container disk
  type: container_disk
  image: quay.io/containerdisks/fedora:43

boot_source:            # imported by CDI; raw DataVolume source (http, registry, pvc, ...)
  type: data_volume
  source:
    http:
      url: https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
  size: 20Gi
  storage_class: fast   # optional, as are volume_mode and access_modes

boot_source:            # cloned from a DataSource (golden image)
  type: data_volume
  source_ref:
    name: win2k25
    namespace: openshift-virtualization-os-images
  size: 60Gi

boot_source:            # an existing PVC, used in place
  type: pvc
  name: my-disk
```

Imported disks are named `<instance>-boot` and deleted with the VM.

### Cloud-init

For `ssh` guests cloud-init is on by default. The management user
(`ssh_user`, the generated key, passwordless sudo, bash) is merged into
`user_data.users` by name: settings you give that user win and lists such as
`ssh_authorized_keys` and `groups` append. Other users are kept.

```yaml
cloud_init:
  user_data:
    hostname: web-1
    packages:
      - nginx
    users:
      - name: cloud-user
        groups:
          - wheel
  network_data:
    version: 2
    ethernets:
      mgmt:
        match:
          macaddress: "02:00:00:00:00:01"
        set-name: mgmt0
        dhcp4: true
```

User and network data are stored in the Secret `<instance>-cloudinit`. Set
`cloud_init.enabled: false` for images without cloud-init.

### Data disks

```yaml
data_disks:
  - name: scratch       # disk and volume name
    size: 10Gi
  - name: seeded
    size: 5Gi
    source:
      http:
        url: https://example.com/data.qcow2
    bus: scsi
```

Without `source` or `source_ref` the disk is blank. `storage_class`,
`volume_mode`, `access_modes` and `boot_order` are also accepted. CDI's
StorageProfile for the storage class must define access modes, or the
DataVolume never binds.

### Ports and endpoints

`NodePort` (the default) creates a Service with a `management` port and one
port per `application_ports` entry, and connects to the first Ready node's
InternalIP unless `connection_ip` is set. Reading nodes needs cluster-scoped
RBAC; setting `connection_ip` avoids it.

`PodIP` creates no Service and connects to the VMI's pod network address.
Use it when the controller runs inside the cluster.

`None` creates no Service and connects to `connection_ip`, for example a
Multus address:

```yaml
interfaces:
  - name: lab
    bridge: {}
networks:
  - name: lab
    multus:
      networkName: lab-bridge
ssh_service:
  type: None
connection_ip: 192.0.2.10
```

Converge and verify see each application port as
`mp_kubevirt_endpoints.<name>` with `host`, `port` (the NodePort, or the
guest port without a Service), `guest_port` and `protocol`.

## Windows guests

Use a sysprep-generalized golden image with WinRM over HTTPS enabled.

```yaml
mp:
  kubevirt:
    boot_source:
      type: data_volume
      source_ref:
        name: win2k25
        namespace: openshift-virtualization-os-images
      size: 60Gi
    instancetype: u1.large
    preference: windows.2k25
    connection: psrp          # or winrm
    sysprep_secret: windc01-sysprep
    connection_vars:
      ansible_user: Administrator
      ansible_password: "{{ lookup('ansible.builtin.env', 'WINDOWS_ADMIN_PASSWORD') }}"
```

- psrp and winrm connect to port 5986 over HTTPS with NTLM and skip
  certificate validation. Anything in `connection_vars` overrides that.
- Cloud-init is off unless `cloud_init.enabled` is true; no SSH key is
  injected.
- `sysprep_secret` must exist before create; it holds `unattend.xml` (and
  optionally `autounattend.xml`) that sets the administrator password.
- Specialize and first-logon commands take minutes; prepare waits
  `mp_kubevirt_windows_wait_timeout` seconds.

## vm_overrides

The single escape hatch for anything not covered above. It is merged over the
rendered VirtualMachine with `combine(recursive=true, list_merge='append_rp')`:
dicts merge, and list items are appended. Use it to add disks, volumes,
interfaces and networks, firmware, or labels:

```yaml
vm_overrides:
  metadata:
    labels:
      team: qa
  spec:
    template:
      spec:
        domain:
          firmware:
            bootloader:
              efi:
                secureBoot: false
          devices:
            disks:
              - name: scratch
                disk:
                  bus: virtio
        volumes:
          - name: scratch
            emptyDisk:
              capacity: 5Gi
```

Do not change the name, namespace or labels the role sets: destroy finds
resources by them.

## RBAC

The identity in `KUBECONFIG` needs, in each namespace used:

| API group | Resources | Verbs |
| --- | --- | --- |
| `kubevirt.io` | `virtualmachines` | get, list, create, patch, delete, watch |
| `kubevirt.io` | `virtualmachineinstances` | get, list (PodIP) |
| `cdi.kubevirt.io` | `datavolumes` | get, list, create, delete (data volumes) |
| core | `secrets`, `services` | get, list, create, patch, delete |

Plus cluster-scoped `get, list` on `nodes` for `NodePort` without
`connection_ip`. Cloning from a DataSource in another namespace also needs
CDI clone permission there.

## Testing

`extensions/molecule/kubevirt` is the self-test. Run it with `make test-kubevirt`
against the cluster in `KUBECONFIG`, or with `scripts/test-kubevirt-kind.sh`,
which creates a disposable kind cluster with KubeVirt and CDI and deletes it
afterwards.
