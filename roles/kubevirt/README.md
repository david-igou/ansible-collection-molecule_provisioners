# `david_igou.molecule_provisioners.kubevirt`

Molecule provisioner role for KubeVirt VMs. Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers (which set `PROVISIONER=kubevirt`).

Requires:
- A reachable Kubernetes cluster with KubeVirt installed and a working `KUBECONFIG`.
- `kubernetes.core` and `community.crypto` collections (declared as deps in `galaxy.yml`).

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Generates SSH keypair, creates VirtualMachine + NodePort Service per platform, writes molecule inventory |
| `destroy` | Deletes VirtualMachine and NodePort Service per platform |
| `prepare` | `wait_for_connection` against each created host |

## Inputs (per-platform, in `molecule.yml`)

```yaml
platforms:
  - name: ubuntu-24
    kubevirt:
      image: quay.io/containerdisks/ubuntu:24.04   # required, containerdisk image
      namespace: molecule                          # required
      ansible_user: cloud-user                     # required (cloud-init creates this user)
      memory: 4Gi                                  # required
      disk_size: 30Gi                              # required
      ssh_service:
        type: NodePort                             # only NodePort supported in v1
```

## Role-level overrides

See `defaults/main.yml`.

## v1 limitation

Only `ssh_service.type: NodePort` is supported. The role asserts this on create. LoadBalancer / ClusterIP+port-forward are out of scope for v1.
