# `david_igou.molecule_provisioners.kubevirt`

Molecule provisioner role for KubeVirt VMs. Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers, which read `mp_backend` from the molecule group's hostvars.

Requires:

- A reachable Kubernetes cluster with KubeVirt installed and a working `KUBECONFIG`.
- `kubernetes.core` and `community.crypto` collections (declared in `galaxy.yml`).

### RBAC required by the service account in `KUBECONFIG`

The minimal verb set with the default `container_disk` boot source:

| Scope | Resource | Verbs |
| --- | --- | --- |
| cluster | `nodes` | `get`, `list` |
| namespace (`mp.kubevirt.namespace`, default `molecule`) | `virtualmachines.kubevirt.io` | `create`, `get`, `delete` |
| namespace | `services` | `create`, `get`, `delete` |
| namespace | `virtualmachineinstances.kubevirt.io` | `get` |

`secrets` access is **not** required in `container_disk` mode — the role injects the SSH key via cloud-init userData on the VM spec, not as a Kubernetes Secret. The `data_volume_url` / `data_volume_pvc` / `data_volume_source_ref` modes additionally need `datavolumes.cdi.kubevirt.io [create, get, delete]` in `mp.kubevirt.namespace`.

`data_volume_source_ref` clones a golden image across namespaces (the `DataSource` and its backing PVC live in the OS-images namespace, e.g. `openshift-virtualization-os-images`). CDI enforces a cross-namespace authorization check for this: the service account in `KUBECONFIG` additionally needs `create` on the **`datavolumes/source`** subresource in the **source** namespace (`source_ref.namespace`), on top of the `datavolumes [create, get, delete]` grant in `mp.kubevirt.namespace`. Without it, the DataVolume is created but stalls and the CDI controller reports an authorization/`clone` error. (`data_volume_pvc` needs the same `datavolumes/source create` in its `source.namespace` when cloning cross-namespace.)

The cluster-scoped `nodes` requirement is currently the tight spot for least-privilege namespaced setups (it's used to pick the NodePort connection IP). See [issue #30](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/30) for an opt-out via `mp.kubevirt.connection_ip`.

> **OpenShift note:** prefer `oc auth can-i …` over `kubectl auth can-i …` to preflight these. OpenShift adds a separate authorization layer that `kubectl`'s SubjectAccessReview can return false negatives for; `oc` is authoritative.

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Computes per-host merged specs, generates an SSH keypair, creates VirtualMachine + NodePort Service per host in `groups['molecule']`, writes runtime connection details (`ansible_host`, `ansible_port`, etc.) into the runtime inventory file. |
| `destroy` | Deletes VirtualMachine and NodePort Service per host. |
| `prepare` | `wait_for_connection` against each created host (honors the per-host connection plugin: ssh/psrp/winrm). Windows hosts get the longer `mp_kubevirt_windows_wait_timeout`. |

## Inputs (per-host, in inventory)

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
                port: 22                       # only consulted when type=None; default 22
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
              instancetype: u1.medium          # str OR {name, kind}
              preference: fedora               # str OR {name, kind}

              # Scheduling
              node_selector: {kubernetes.io/arch: amd64}
              tolerations: []
              affinity: {}

              # Appended to defaults (containerdisk + cloudinitdisk + default pod net)
              extra_disks: []
              extra_volumes: []
              extra_interfaces: []
              extra_networks: []

              # Escape hatch — deep-merged into the whole VirtualMachine object
              # (lists append). Use for anything not surfaced above.
              vm_overrides: {}
```

Shared defaults can be hoisted into `mp_defaults.kubevirt` in `inventory/group_vars/molecule.yml`. Field resolution: role defaults ← `mp_defaults.kubevirt` ← `hostvars[item].mp.kubevirt`.

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
  source: {name: golden-ubuntu, namespace: images}
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

> **Security scope — cert validation off is intentional here.** These are
> ephemeral, network-isolated Molecule test guests whose self-signed WinRM
> certificate was minted seconds earlier by the unattend at first boot, so there
> is no trust anchor to validate against and nothing durable to protect. This is
> a deliberate test-scope behavior — **do not copy `ansible_psrp_cert_validation:
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
    cpu: {cores: 4}
```

What changes when `connection != ssh`:

- **No cloud-init.** The renderer omits the `cloudinitdisk` disk/volume entirely
  (Windows goldens have no cloud-init, and a stray cloudinit disk shifts disk
  ordering and confuses boot). No SSH keypair is generated when *no* host uses ssh.
- **Sysprep volume.** When `sysprep_secret` is set, a `cdrom` disk named `sysprep`
  plus a volume `{sysprep: {secret: {name: <sysprep_secret>}}}` is attached, after
  the boot disk. **CRITICAL KubeVirt gotcha:** the field is `secret.name` — the API
  silently drops `secretName` and the VMI wedges `Pending`. (`sysprep_secret` is
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
  ignore`, and generous connection/read timeouts. The file is written `0600` with
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

## Escape hatch and foot-guns

`vm_overrides` is deep-merged into the whole VirtualMachine object with `list_merge='append'`. There are no guardrails — overriding any of the following will break the lifecycle:

- **Don't set `spec.running: false`.** The prepare phase calls `wait_for_connection` against the NodePort SSH service; a stopped VM never becomes reachable.
- **Don't replace the `cloudinitdisk` volume.** The role injects an SSH public key via cloud-init `users:`. If you must edit it, replicate the block and keep `temporary_ssh_public_key`.
- **Don't change `metadata.labels.kubevirt.io/domain` or the SSH Service's selector.** The NodePort routes by this label.

When `instancetype` is set, the renderer **omits** `domain.cpu` and `domain.resources` from the rendered spec — KubeVirt rejects conflicting fields. Setting `cpu:`/`memory_limit:` alongside `instancetype:` is silently ignored (a debug message is emitted at validate time).

## Skipping the Node lookup

By default the role lists cluster `Node`s once to pick an `InternalIP` for the NodePort connection. This needs cluster-scoped `nodes [get,list]` RBAC. Namespace-scoped service accounts can opt out by setting `connection_ip` on every host — the role then skips the Node lookup entirely:

```yaml
mp:
  kubevirt:
    boot_source: {type: container_disk, image: quay.io/containerdisks/ubuntu:24.04}
    connection_ip: 192.0.2.10 # e.g. cluster ingress IP, controller-reachable Node IP, etc.
```

If even one host omits `connection_ip`, the Node lookup still runs (the rest of the cluster nodes is unchanged); only the host with `connection_ip` set bypasses it for its own `ansible_host`.

## Role-level overrides

See `defaults/main.yml` (`mp_kubevirt_role_defaults`, `mp_kubevirt_ssh_key_path`, `mp_kubevirt_wait_timeout`, `mp_kubevirt_windows_wait_timeout`, `mp_kubevirt_allowed_ssh_service_types`, `mp_kubevirt_allowed_connections`).

## SSH service types

Three modes are supported:

- **`NodePort`** (default): the role creates a `NodePort` Service per VM and resolves `ansible_host` to a cluster Node InternalIP (or to `connection_ip` if set).
- **`None`**: no Service is created. `connection_ip` is required (the role asserts this at validate time). `ansible_port` defaults to the guest port implied by the connection type — `22` for `ssh`, `5986` for `psrp`/`winrm` — override per-host with `ssh_service.port`. Use this for setups where access is provided by an external Route/Ingress, or where the controller can talk to pod IPs directly.
- **`PodIP`**: no Service is created and no `connection_ip` is needed — the role waits for the VMI to report its pod-network address (`status.interfaces[0].ipAddress`, the masquerade/virt-launcher pod IP) and connects there directly. Only works when the controller runs **inside the cluster** (e.g. an OpenShift Dev Spaces workspace or CI pod). This is the least-privilege mode: the driving ServiceAccount needs neither `services` nor cluster-wide `nodes` RBAC — just VMs/VMIs in the target namespace. Port defaults as in `None` mode (`ssh_service.port` override honored). Lookup bounds: `mp_kubevirt_podip_lookup_retries` (60) × `mp_kubevirt_podip_lookup_delay` (5s).

```yaml
mp:
  kubevirt:
    boot_source: {type: container_disk, image: quay.io/containerdisks/ubuntu:24.04}
    ssh_service:
      type: None
      port: 2222 # only if the Route/Ingress maps to a non-default port
    connection_ip: route.example.com # or a controller-reachable pod IP
```

`LoadBalancer` / `ClusterIP`+port-forward are out of scope.
