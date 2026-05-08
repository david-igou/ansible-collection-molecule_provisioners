# `david_igou.molecule_provisioners.podman`

Molecule provisioner role for podman containers. Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers (which set `PROVISIONER=podman`).

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Creates user-defined podman networks (when set), then creates containers from `molecule_yml.platforms[].podman.*` |
| `destroy` | Removes those containers and any non-reserved networks |
| `prepare` | Installs `sudo` inside each container |

## Inputs (per-platform, in `molecule.yml`)

```yaml
platforms:
  - name: ubuntu-24
    podman:
      image: docker.io/...:tag         # required
      command: sleep 1d                # optional
      privileged: false                # optional, default false
      volumes: []                      # optional
      capabilities: []                 # optional
      podman_network: []               # optional, list or single string
      env: {}                          # optional
      tmpfs: []                        # optional
      exposed_ports: []                # optional
      published_ports: []              # optional
```

## Role-level overrides

See `defaults/main.yml`.
