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

`secrets` access is **not** required in `container_disk` mode — the role injects the SSH key via cloud-init userData on the VM spec, not as a Kubernetes Secret. The `data_volume_url` / `data_volume_pvc` modes additionally need `datavolumes.cdi.kubevirt.io [create, get, delete]`.

The cluster-scoped `nodes` requirement is currently the tight spot for least-privilege namespaced setups (it's used to pick the NodePort connection IP). See [issue #30](https://github.com/david-igou/ansible-collection-molecule_provisioners/issues/30) for an opt-out via `mp.kubevirt.connection_ip`.

> **OpenShift note:** prefer `oc auth can-i …` over `kubectl auth can-i …` to preflight these. OpenShift adds a separate authorization layer that `kubectl`'s SubjectAccessReview can return false negatives for; `oc` is authoritative.

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Computes per-host merged specs, generates an SSH keypair, creates VirtualMachine + NodePort Service per host in `groups['molecule']`, writes runtime connection details (`ansible_host`, `ansible_port`, etc.) into the runtime inventory file. |
| `destroy` | Deletes VirtualMachine and NodePort Service per host. |
| `prepare` | `wait_for_connection` against each created host. |

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
              # data_volume_pvc, pvc). See "Boot sources" below.
              boot_source:
                type: container_disk
                image: quay.io/containerdisks/ubuntu:24.04

              # Optional
              namespace: molecule              # role default 'molecule'
              ssh_user: cloud-user             # role default 'cloud-user'
              ssh_service:
                type: NodePort                 # only NodePort in v1

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

### `pvc` — direct mount of existing PVC

No CDI required.

```yaml
boot_source:
  type: pvc
  name: existing-boot-pvc
```

## Escape hatch and foot-guns

`vm_overrides` is deep-merged into the whole VirtualMachine object with `list_merge='append'`. There are no guardrails — overriding any of the following will break the lifecycle:

- **Don't set `spec.running: false`.** The prepare phase calls `wait_for_connection` against the NodePort SSH service; a stopped VM never becomes reachable.
- **Don't replace the `cloudinitdisk` volume.** The role injects an SSH public key via cloud-init `users:`. If you must edit it, replicate the block and keep `temporary_ssh_public_key`.
- **Don't change `metadata.labels.kubevirt.io/domain` or the SSH Service's selector.** The NodePort routes by this label.

When `instancetype` is set, the renderer **omits** `domain.cpu` and `domain.resources` from the rendered spec — KubeVirt rejects conflicting fields. Setting `cpu:`/`memory_limit:` alongside `instancetype:` is silently ignored (a debug message is emitted at validate time).

## Role-level overrides

See `defaults/main.yml` (`mp_kubevirt_role_defaults`, `mp_kubevirt_ssh_key_path`, `mp_kubevirt_wait_timeout`, `mp_kubevirt_allowed_ssh_service_types`).

## v1 limitation

Only `ssh_service.type: NodePort` is supported. The role asserts this on create. LoadBalancer / ClusterIP+port-forward are out of scope for v1.
