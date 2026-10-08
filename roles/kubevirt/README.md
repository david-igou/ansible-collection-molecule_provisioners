# `david_igou.molecule_provisioners.kubevirt`

Molecule provisioner role for KubeVirt VMs. Use the collection's `playbooks/{create,destroy,prepare}.yml` dispatchers to invoke this role. They select the backend using `mp_backend` from the molecule group's hostvars.

Requires:

- A reachable Kubernetes cluster with KubeVirt installed and a working `KUBECONFIG`.
- `kubernetes.core` and `community.crypto` collections (declared in `galaxy.yml`).

### RBAC required by the service account in `KUBECONFIG`

Permissions for the default `container_disk` boot source and `NodePort` mode,
including retries and cleanup:

| Scope | Resource | Verbs |
| --- | --- | --- |
| cluster | `nodes` | `get`, `list` |
| namespace (`mp.kubevirt.namespace`, default `molecule`) | `virtualmachines.kubevirt.io` | `create`, `get`, `patch`, `delete` |
| namespace | `services` | `create`, `get`, `patch`, `delete` |
| namespace | `virtualmachineinstances.kubevirt.io` | `get` |

`secrets` access is **not** required in `container_disk` mode — the role injects the SSH key via cloud-init userData on the VM spec, not as a Kubernetes Secret. The `data_volume_url` / `data_volume_pvc` / `data_volume_source_ref` modes additionally need `datavolumes.cdi.kubevirt.io [create, get, delete]` in `mp.kubevirt.namespace`.

`data_volume_source_ref` clones a golden image across namespaces (the `DataSource` and its backing PVC live in the OS-images namespace, e.g. `openshift-virtualization-os-images`). CDI enforces a cross-namespace authorization check for this: the service account in `KUBECONFIG` additionally needs `create` on the **`datavolumes/source`** subresource in the **source** namespace (`source_ref.namespace`), on top of the `datavolumes [create, get, delete]` grant in `mp.kubevirt.namespace`. Without it, the DataVolume is created but stalls and the CDI controller reports an authorization/`clone` error. (`data_volume_pvc` needs the same `datavolumes/source create` in its `source.namespace` when cloning cross-namespace.)

The cluster-scoped `nodes` permission is used to pick the NodePort connection IP. Set `mp.kubevirt.connection_ip` to skip that lookup; see [issue #30](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/30).

> **OpenShift note:** use `oc auth can-i …` to check these permissions against your target cluster and identity.

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Merges per-host specs, generates a keypair for simple-variable SSH guests or explicit key injection, creates each VirtualMachine and its Service in NodePort mode, and writes runtime connection details into the inventory file. Full definitions use caller-provided guest access. |
| `destroy` | Deletes the run's VirtualMachines and NodePort Services, waits for generated resources to disappear, then removes local run state and runtime inventory. Fixed-name mode deletes resources using the original inventory names. |
| `prepare` | `wait_for_connection` against each created host (honors the per-host connection plugin: ssh/psrp/winrm). Windows hosts get the longer `mp_kubevirt_windows_wait_timeout`. |

## Concurrent runs in a shared namespace

Run isolation is enabled by default. Select KubeVirt in
`inventory/group_vars/molecule.yml`; no isolation flag is needed:

```yaml
mp_backend: kubevirt
```

Each run saves `kubevirt_run.yml` (mode `0600`) in its Molecule ephemeral
directory **before** creating infrastructure. The file contains only the run
ID and host-to-resource mapping. VMs and NodePort Services use a normalized host
prefix, a host-name digest and a random run suffix. Curated boot DataVolumes/PVCs
add `-boot`; full-definition disk names use the format described below.
Names stay within Kubernetes limits even for long inventory names.
Logical inventory hosts, groups and consumer variables are unchanged. Runtime
connection inventory still uses names such as `instance`.

Separate concurrent runs need separate Molecule ephemeral directories. Two
processes must not simultaneously create/destroy the **same** ephemeral
directory. Retrying create in the same directory reuses the saved identity and
SSH key. Destroy uses saved namespaces and resource requirements, even if the
inventory changes or isolation is subsequently disabled. Create rejects changed
host sets, namespaces, Service modes or generated-disk ownership until destroy.

VMs, VMIs, generated DataVolumes and Services carry
`molecule-provisioners.igou.io/run-id`. Before reusing or deleting an existing
resource, the role checks its ownership. Deletes use UID and resource-version
preconditions and foreground garbage collection. Cleanup waits for generated
VMIs, DataVolumes and PVCs; referenced external PVCs, Secrets and extra volumes
are not deleted. Isolated generated-disk runs additionally need `get` on PVCs.
Retries against existing VMs/Services need `patch` permission, as in fixed mode.
`mp_kubevirt_cleanup_timeout` defaults to 120 seconds per deletion/wait.

State and runtime inventory are removed only after cleanup succeeds. A failed
create, interrupted Ansible process, finalizer timeout or ownership mismatch
leaves the saved identity intact. Retry `molecule destroy` with the same scenario
and ephemeral directory after resolving the failure. Preserve that directory
outside short-lived CI workspaces until destroy has completed; do not run
`molecule reset` or erase it while resources remain.

If state is lost, isolated destroy performs no deletion; it never falls back to
fixed names. Restore `kubevirt_run.yml` from a backup if possible. Otherwise,
identify the orphan's run ID and exact resources before deleting them manually:

```bash
oc get vm,svc,dv,pvc -n molecule -l molecule-provisioners.igou.io/run-id
oc get vm,svc,dv,pvc -n molecule -l molecule-provisioners.igou.io/run-id=<run-id>
```

Inspect the selected VM, its generated DataVolumes/PVC owner references, and
Services. Delete only that run's exact VM/Service names with foreground
propagation, then verify its generated descendants are gone. PVCs may be found
through DataVolume owner references rather than the run label. Do not delete
external claims or another run's resources. Creating after state loss starts a
new run and does not adopt or clean up the orphan.

For fixed Kubernetes names, set `mp_kubevirt_run_isolation: false` in
`inventory/group_vars/molecule.yml`. Before upgrading, destroy existing
fixed-name guests with the old collection version. If already upgraded, set
the flag to `false` and destroy with the original inventory, then remove the
override to use isolation on the next create. With isolation enabled,
`vm_overrides` cannot change VM names, namespaces, domain selectors or generated
`dataVolumeTemplates`; custom external volume references retain their names.

## Inputs (per-host, in inventory)

Minimal `inventory/hosts.yml` (paired with `mp_backend: kubevirt` above):

```yaml
---
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            kubevirt:
              namespace: molecule
              boot_source:
                type: container_disk
                image: quay.io/containerdisks/ubuntu:24.04
              ssh_user: ubuntu
              memory: 1Gi
```

`instance` stays the Ansible inventory name. The VM and Service receive names
such as `instance-5e8c03a9-825bbd211b673a09` automatically.

The simple-variable per-host settings (use `vm_definition` instead for a full manifest):

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            kubevirt:
              # Required: boot source (one of container_disk, data_volume_url,
              # data_volume_pvc, data_volume_source_ref, pvc). See "Boot
              # sources" below.
              boot_source:
                type: container_disk
                image: quay.io/containerdisks/ubuntu:24.04

              # Optional
              namespace: molecule              # role default 'molecule'
              ssh_user: cloud-user             # role default 'cloud-user' (ssh connection)
              ssh_service:
                type: NodePort                 # 'NodePort', 'None', or 'PodIP'
                port: 22                       # consulted in None/PodIP mode; default 22
                                               # (5986 for psrp/winrm connections)
              connection_ip: 192.0.2.10        # optional with NodePort, REQUIRED with None.
                                               # When set, skips the cluster-scoped Node
                                               # lookup for this host (saves the SA's
                                               # nodes [get,list] RBAC requirement). See
                                               # "Skipping the Node lookup" below.

              # Guest connection (see "Windows guests" below)
              connection: ssh                  # ssh (default) | psrp | winrm
              admin_user: Administrator        # psrp/winrm only; default 'Administrator'
              admin_password: "{{ ... }}"      # psrp/winrm only; REQUIRED (sensitive)
              sysprep_secret: win2k25-sysprep  # optional; attach a KubeVirt sysprep
                                               # cdrom volume from this Secret name

              # Curated compute
              cpu:
                cores: 4                       # default 2
                sockets: 1
                threads: 1
              memory: 1Gi                      # → resources.requests.memory
              memory_limit: 2Gi                # → resources.limits.memory

              # Compute presets (alternative to cpu/memory; suppresses both)
              instancetype: u1.medium          # string or mapping with name and kind
              preference: fedora               # string or mapping with name and kind

              # Scheduling
              node_selector:
                kubernetes.io/arch: amd64
              tolerations: []
              affinity: {}

              # Optional bootstrap and primary networking; see sections below.
              cloud_init: {}
              interfaces:
                - name: default
                  masquerade: {}
              networks:
                - name: default
                  pod: {}

              # Appended after the selected boot/bootstrap disks and networks
              extra_disks: []
              extra_volumes: []
              extra_interfaces: []
              extra_networks: []

              # Escape hatch — deep-merged into the whole VirtualMachine object
              # (lists append). Use for anything not surfaced above.
              vm_overrides: {}
```

Set shared defaults in `mp_defaults.kubevirt` in `inventory/group_vars/molecule.yml`. Field resolution: role defaults ← `mp_defaults.kubevirt` ← `hostvars[item].mp.kubevirt`.

## Full VirtualMachine definitions

Set `mp.kubevirt.vm_definition` to a desired-state `kubevirt.io/v1`
`VirtualMachine` manifest when the scenario needs complete control over the VM.
The role skips its VM generator: it does not add compute settings, disks,
interfaces, cloud-init, or `spec.running`, and it does not merge `vm_overrides`.
KubeVirt validates the supplied spec against the cluster's installed API.

The role still manages resource identity, connection inventory, prepare, and
destroy. `namespace`, `ssh_user`, `connection`, `ssh_service`, `connection_ip`,
`admin_user`, and `admin_password` remain outside the manifest. Only connection
and namespace defaults apply in this mode. VM-building parameters such as
`boot_source`, `memory`, `cloud_init`, `sysprep_secret`, the scheduling/networking
parameters, `extra_*`, and `vm_overrides` are mutually exclusive with
`vm_definition`, including values inherited from `mp_defaults.kubevirt`.

For SSH, supply an existing private key at `mp_kubevirt_ssh_key_path` before
create, and arrange for the guest to accept its public key. The role does not
generate or inject a key for full definitions. In a mixed scenario, the same
key is used by the simple-variable guests; use an Ed25519 key for that case.
Secrets and other external dependencies must exist before provisioning.

This example assumes `molecule-bootstrap` contains cloud-init `userdata` that
configures the `ubuntu` user's SSH access with the supplied key and installs
Python. NodePort management is the default:

```yaml
---
all:
  children:
    molecule:
      vars:
        mp_backend: kubevirt
        mp_kubevirt_ssh_key_path: /path/to/scenario-key
      hosts:
        ubuntu-test:
          mp:
            kubevirt:
              namespace: molecule
              ssh_user: ubuntu
              vm_definition:
                apiVersion: kubevirt.io/v1
                kind: VirtualMachine
                spec:
                  runStrategy: RerunOnFailure
                  template:
                    spec:
                      domain:
                        cpu:
                          cores: 2
                        resources:
                          requests:
                            memory: 2Gi
                        devices:
                          rng: {}
                          interfaces:
                            - name: management
                              masquerade: {}
                          disks:
                            - name: root
                              disk:
                                bus: virtio
                            - name: seed
                              disk:
                                bus: virtio
                      networks:
                        - name: management
                          pod: {}
                      volumes:
                        - name: root
                          containerDisk:
                            image: quay.io/containerdisks/ubuntu:24.04
                        - name: seed
                          cloudInitNoCloud:
                            secretRef:
                              name: molecule-bootstrap
```

`metadata.name` is optional and always replaced with the managed VM name:
the inventory host name in fixed-name mode, or the generated name under run
isolation. Omit `metadata.namespace`, or match `mp.kubevirt.namespace`.
The role sets the domain selector and, in isolated mode, ownership labels on
the VM and its template while retaining other labels and annotations.
Omit `status`; this parameter describes desired state rather than an API dump.

Under run isolation, each `spec.dataVolumeTemplates` name becomes the first
40 characters of the managed VM name plus `-dv-` and a 16-character SHA-256
digest of the managed VM name and original disk name. Matching `dataVolume`
and `persistentVolumeClaim` volume references are rewritten. Template names
must be unique. Fixed-name mode retains their original names. External PVCs,
external DataVolumes, and CDI source references keep their names and remain
caller-owned. Destroy checks ownership and waits for all template-created
DataVolumes and PVCs to disappear; saved runs from earlier versions still work.

NodePort and PodIP management require pod networking in the full definition
(including KubeVirt's automatic pod interface when networking is omitted).
For Multus-only guests, use `ssh_service.type: None` with `connection_ip`.
The guest must be running and reachable for prepare to succeed; a `Halted`
or `Manual` strategy requires the caller to start it before prepare.

To load an existing manifest from a file, supply a mapping through inventory:

```yaml
vm_definition: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/vm.yml') | from_yaml }}"
```

## Boot sources

### `container_disk` — OCI-packaged image

```yaml
boot_source:
  type: container_disk
  image: quay.io/containerdisks/ubuntu:24.04
```

### `data_volume_url` — CDI import from URL

Requires CDI installed on the cluster.

```yaml
boot_source:
  type: data_volume_url
  url: https://cloud-images.ubuntu.com/.../noble.img
  size: 10Gi                  # required
  storage_class: standard     # optional
```

### `data_volume_pvc` — CDI smart-clone from existing PVC

Requires CDI installed on the cluster.

```yaml
boot_source:
  type: data_volume_pvc
  source:
    name: golden-ubuntu
    namespace: images
  size: 10Gi                  # required
  storage_class: standard     # optional
```

### `data_volume_source_ref` — CDI clone from a DataSource (golden image)

Requires CDI installed on the cluster. Boots from a CDI `DataSource` (golden
image) via `dataVolumeTemplates[].spec.sourceRef`. Use this instead of
`data_volume_pvc` when the golden-image PVC has a rolling name managed by a
`DataImportCron` (e.g. `centos-stream10-1fcd75f226b4`) — the `DataSource` is a
stable indirection that always points at the current PVC, so the static PVC name
in `data_volume_pvc` can't track it.

```yaml
boot_source:
  type: data_volume_source_ref
  source_ref:
    name: centos-stream10                             # DataSource name (required)
    namespace: openshift-virtualization-os-images     # required
    kind: DataSource                                  # optional, default DataSource
  size: 30Gi                  # required (storage request)
  storage_class: ""           # optional, same semantics as data_volume_url
```

Renders a `dataVolumeTemplates` entry whose `spec.sourceRef` is `{kind, name,
namespace}` and whose `spec.storage.resources.requests.storage` is `size`
(`storageClassName` is added only when `storage_class` is set). Because the
`DataSource` lives in a different namespace, this needs the cross-namespace CDI
authorization grant — see the RBAC section above (`datavolumes/source create` in
`source_ref.namespace`).

### `pvc` — direct mount of existing PVC

No CDI required.

```yaml
boot_source:
  type: pvc
  name: existing-boot-pvc
```

## Windows guests (psrp / winrm)

Set `connection: psrp` (or `winrm`) to provision a Windows Server 2025 / Windows 11
test VM from a **sysprep-generalized golden image**. A generalized clone boots
into OOBE and is specialized at first boot by a KubeVirt **sysprep** volume — a
`Secret` carrying `unattend.xml`, which Windows OOBE auto-consumes
from the attached removable media. The unattend sets a local administrator
password; Ansible then connects over **WinRM-over-HTTPS** (port 5986, NTLM,
certificate validation off) as that admin.

> **Security scope:** the connection disables certificate validation for
> ephemeral Molecule guests using self-signed WinRM certificates. The role does
> not enforce network isolation. **Do not copy `ansible_psrp_cert_validation:
> ignore` / `ansible_winrm_server_cert_validation: ignore` into a production
> connection configuration**, where a validated certificate chain is expected.

```yaml
mp:
  kubevirt:
    boot_source:
      type: data_volume_source_ref                    # clone a golden DataSource-backed PVC
      source_ref:
        name: win2k25                                 # DataSource for the golden image
        namespace: openshift-virtualization-os-images
      size: 80Gi
    connection: psrp                                   # ssh (default) | psrp | winrm
    admin_user: Administrator                          # default 'Administrator'
    admin_password: "{{ lookup('ansible.builtin.env', 'WIN_ADMIN_PASSWORD') }}"
    sysprep_secret: win2k25-sysprep                    # the Secret carrying unattend.xml
    memory: 8Gi
    cpu:
      cores: 4
```

What changes when `connection != ssh`:

- **No cloud-init by default.** The renderer omits the `cloudinitdisk` disk/volume
  unless `cloud_init.enabled: true` is supplied
  (Windows goldens have no cloud-init, and a stray cloudinit disk shifts disk
  ordering and confuses boot). No SSH keypair is generated when *no* host uses SSH
  or explicitly enables `cloud_init.inject_ssh_key`.
- **Sysprep volume.** When `sysprep_secret` is set, a `cdrom` disk named `sysprep`
  plus a volume referencing `sysprep.secret.name` is attached after
  the boot disk. The field is `secret.name` — the API
  silently drops `secretName` and the VMI stays `Pending`. (`sysprep_secret` is
  valid for any connection, but is primarily used with psrp/winrm.)
- **Service targets 5986.** The per-VM NodePort Service (still keyed `ssh_service`
  in the schema — the name is **historical**, it predates Windows support and is
  kept to avoid breaking every consumer) fronts guest port **5986** instead of 22.
  In `ssh_service.type: None` mode, `ssh_service.port` defaults to 5986 for
  psrp/winrm (22 for ssh).
- **Ephemeral inventory.** The runtime inventory renders `ansible_connection: psrp`
  (or `winrm`) with `ansible_user`/`ansible_password` (the admin credentials),
  `ansible_psrp_auth: ntlm` / `ansible_winrm_transport: ntlm`,
  `ansible_psrp_cert_validation: ignore` / `ansible_winrm_server_cert_validation:
  ignore`, and configured connection/read timeouts. The file is written `0600` with
  `no_log` because the password now lands on disk.
- **Longer prepare wait.** `prepare` uses `wait_for_connection` (which honors the
  connection plugin) but with `mp_kubevirt_windows_wait_timeout` (default **900s**)
  for psrp/winrm hosts — OOBE specialize + the unattend `FirstLogonCommands`
  routinely take several minutes — versus `mp_kubevirt_wait_timeout` (120s) for ssh.

**Controller prerequisites (not shipped by this collection):** psrp needs
[`pypsrp`](https://pypi.org/project/pypsrp/) on the controller (and `pypsrp[credssp]`
only if you switch off NTLM); `winrm` needs [`pywinrm`](https://pypi.org/project/pywinrm/).
These are controller-side runtime deps of the Ansible connection plugins, so they
are intentionally **not** in this collection's `requirements.txt` — install them in
your Molecule execution environment / controller venv.

**RBAC:** a `sysprep_secret` is a `Secret` the *consumer* creates (it is not managed
by this role), so no extra Secret verbs are needed here. The golden-image clone
paths (`data_volume_pvc` / `data_volume_source_ref`) carry their usual CDI grants —
see the RBAC section above.

> **Consumer ordering:** the VM starts immediately (`running: true`), so the sysprep
> `Secret` must exist **before** the create playbook runs. In your scenario
> `create.yml`, create the Secret *before* `import_playbook:
> david_igou.molecule_provisioners.create`.

## Custom cloud-init

SSH guests still receive NoCloud media with the generated public key, the
`ssh_user` account, passwordless sudo, and `/bin/bash`. PSRP/WinRM guests omit
cloud-init unless `cloud_init.enabled: true` is supplied.

`cloud_init.user_data` accepts a cloud-config mapping. The role merges the
management user by name, retaining the generated key alongside custom keys.
Other users, packages, files, and bootstrap commands are passed through.
For example, this installs Python before the prepare phase can complete its
Ansible connection check:

```yaml
mp:
  kubevirt:
    boot_source:
      type: container_disk
      image: quay.io/containerdisks/ubuntu:24.04
    ssh_user: ubuntu
    cloud_init:
      user_data:
        packages:
          - python3
        hostname: molecule-guest
```

`cloud_init.network_data` accepts a cloud-init network-config mapping. This
example supplies a static address on an additional test NIC while the pod
interface remains available for management:

```yaml
cloud_init:
  network_data:
    version: 2
    ethernets:
      management:
        match:
          macaddress: "52:54:00:12:34:01"
        dhcp4: true
      test:
        match:
          macaddress: "52:54:00:12:34:02"
        set-name: test0
        addresses:
          - 192.0.2.10/24
interfaces:
  - name: default
    masquerade: {}
    macAddress: "52:54:00:12:34:01"
extra_interfaces:
  - name: test-lan
    bridge: {}
    macAddress: "52:54:00:12:34:02"
extra_networks:
  - name: test-lan
    multus:
      networkName: test-lan
```

NetworkAttachmentDefinitions belong to the caller. Choose unique MAC addresses
for interfaces sharing the same test LAN.

Existing same-namespace Secrets can supply user data (`userdata` key) or network
data (`networkdata` key):

```yaml
cloud_init:
  user_data_secret: molecule-bootstrap
  network_data_secret: molecule-network
  inject_ssh_key: false
```

Inline data and its corresponding Secret reference are mutually exclusive.
The role neither reads nor changes these Secrets, and destroy leaves them
alone. Create them before provisioning. User-data Secrets require
`inject_ssh_key: false`; the caller must arrange SSH access using the key at
`mp_kubevirt_ssh_key_path`. That path remains the runtime inventory's private
key, even when injection is disabled. Pre-existing Ed25519 keys at that path are reused.
Network-data Secrets can be combined with the default generated SSH bootstrap.

Set `cloud_init.enabled: false` to omit the cloud-init disk and volume. Set
`inject_ssh_key: false` to use your user-data mapping unchanged. These choices
require the image or caller to provide working management access. Inline data
is stored in the VM spec; use Secret references for sensitive bootstrap data.
Provisioning logs suppress the rendered cloud-init data.

The prepare phase waits for Ansible connectivity, not all cloud-init commands.
If converge depends on completed bootstrap, have the scenario wait for
`cloud-init status --wait` after importing the collection's prepare playbook.
See [KubeVirt startup scripts](https://kubevirt.io/user-guide/user_workloads/startup_scripts/).

## Primary interfaces and networks

`interfaces` and `networks` replace the default masquerade/pod lists with raw
KubeVirt interface/network definitions. Names must match one-to-one and remain
unique after appending `extra_interfaces` and `extra_networks`.

For example, keep pod management while selecting a different guest NIC model:

```yaml
interfaces:
  - name: management
    masquerade: {}
    model: e1000
networks:
  - name: management
    pod: {}
```

For a Multus-only guest, use explicit addressing and skip the management
Service. This fragment belongs under `mp.kubevirt`:

```yaml
interfaces:
  - name: test-lan
    bridge: {}
networks:
  - name: test-lan
    multus:
      networkName: test-lan
ssh_service:
  type: "None"
connection_ip: 192.0.2.10
```

The caller supplies the existing NetworkAttachmentDefinition and guest address
through DHCP, cloud-init network data, or image configuration. The controller
must be able to reach that address. NodePort and PodIP management require a pod
network; PodIP resolves its address by configured network name rather than VMI
status ordering. A changed binding must still support the chosen access method.

Supplying both lists as `[]` also disables KubeVirt's automatic pod interface.
Such guests need a caller-provided lifecycle that does not wait for guest
connectivity. These settings do not alter run-scoped names or cleanup ownership.
See [KubeVirt interfaces and networks](https://kubevirt.io/user-guide/network/interfaces_and_networks/).

## VirtualMachine overrides

`vm_overrides` is deep-merged into the whole VirtualMachine object with `list_merge='append'`. Fixed-name mode permits unrestricted overrides. Run isolation reserves resource identity fields; the following changes can still break connectivity:

- **Don't set `spec.running: false`.** The prepare phase calls `wait_for_connection` against the NodePort SSH service; a stopped VM never becomes reachable.
- **Use `cloud_init` to customize bootstrap.** A `vm_overrides` volume with the same name appends a duplicate rather than replacing the existing volume. Use `interfaces`/`networks` to replace primary networking.
- **Don't change `metadata.labels.kubevirt.io/domain` or the SSH Service's selector.** The NodePort routes by this label.

When `instancetype` is set, the renderer **omits** `domain.cpu` and `domain.resources` from the rendered spec — KubeVirt rejects conflicting fields. Setting `cpu:`/`memory_limit:` alongside `instancetype:` is silently ignored (a debug message is emitted at validate time).

## Skipping the Node lookup

By default the role lists cluster `Node`s once to pick an `InternalIP` for the NodePort connection. This needs cluster-scoped `nodes [get,list]` RBAC. Namespace-scoped service accounts can opt out by setting `connection_ip` on every NodePort host — the role then skips the Node lookup entirely:

```yaml
mp:
  kubevirt:
    boot_source:
      type: container_disk
      image: quay.io/containerdisks/ubuntu:24.04
    connection_ip: 192.0.2.10 # e.g. cluster ingress IP, controller-reachable Node IP, etc.
```

If any NodePort host omits `connection_ip`, the Node lookup runs. `None` and `PodIP` hosts do not trigger it; a NodePort host with `connection_ip` still uses its own address.

## Role-level overrides

See `defaults/main.yml` (`mp_kubevirt_role_defaults`, `mp_kubevirt_ssh_key_path`, `mp_kubevirt_wait_timeout`, `mp_kubevirt_windows_wait_timeout`, `mp_kubevirt_allowed_ssh_service_types`, `mp_kubevirt_allowed_connections`, `mp_kubevirt_run_isolation`, `mp_kubevirt_cleanup_timeout`, `mp_kubevirt_podip_lookup_retries`, `mp_kubevirt_podip_lookup_delay`).

## SSH service types

Three modes are supported:

- **`NodePort`** (default): the role creates a `NodePort` Service per VM and resolves `ansible_host` to a cluster Node InternalIP (or to `connection_ip` if set).
- **`None`**: no Service is created. `connection_ip` is required (the role asserts this at validate time). `ansible_port` defaults to the guest port implied by the connection type — `22` for `ssh`, `5986` for `psrp`/`winrm` — override per-host with `ssh_service.port`. Use this for setups where access is provided by an external Route/Ingress, or where the controller can talk to pod IPs directly.
- **`PodIP`**: no Service is created and no `connection_ip` is needed — the role waits for the VMI to report the configured pod network's IP and connects there directly. Only works when the controller runs **inside the cluster** (e.g. an OpenShift Dev Spaces workspace or CI pod). The driving ServiceAccount needs neither `services` nor cluster-wide `nodes` RBAC; it still needs the VM/VMI and boot-source permissions listed above. Port defaults as in `None` mode (`ssh_service.port` override honored). Lookup bounds: `mp_kubevirt_podip_lookup_retries` (60) × `mp_kubevirt_podip_lookup_delay` (5s).

```yaml
mp:
  kubevirt:
    boot_source:
      type: container_disk
      image: quay.io/containerdisks/ubuntu:24.04
    ssh_service:
      type: None
      port: 2222 # only if the Route/Ingress maps to a non-default port
    connection_ip: route.example.com # or a controller-reachable pod IP
```

`LoadBalancer` / `ClusterIP`+port-forward are out of scope.
