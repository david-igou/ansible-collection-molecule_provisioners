# `david_igou.molecule_provisioners.kubevirt`

Molecule provisioner role for KubeVirt VMs. Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers, which read `mp_backend` from the molecule group's hostvars.

Requires:

- A reachable Kubernetes cluster with KubeVirt installed and a working `KUBECONFIG`.
- `kubernetes.core` and `community.crypto` collections (declared in `galaxy.yml`).

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
              image: quay.io/containerdisks/ubuntu:24.04   # required, containerdisk image
              namespace: molecule                          # optional, role default 'molecule'
              ssh_user: cloud-user                         # optional, role default 'cloud-user'
              memory: 1Gi                                  # optional, role default '1Gi'
              ssh_service:
                type: NodePort                             # optional, only NodePort supported in v1
```

Shared defaults can be hoisted into `mp_defaults.kubevirt` in `inventory/group_vars/molecule.yml`. Field resolution order: role defaults <- `mp_defaults.kubevirt` <- `hostvars[item].mp.kubevirt`.

## Role-level overrides

See `defaults/main.yml` (`mp_kubevirt_role_defaults`, `mp_kubevirt_ssh_key_path`, `mp_kubevirt_wait_timeout`, `mp_kubevirt_allowed_ssh_service_types`).

## v1 limitation

Only `ssh_service.type: NodePort` is supported. The role asserts this on create. LoadBalancer / ClusterIP+port-forward are out of scope for v1.
