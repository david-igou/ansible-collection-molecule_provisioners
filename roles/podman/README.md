# `david_igou.molecule_provisioners.podman`

Molecule provisioner role for podman containers. Not invoked directly — invoked via the collection's top-level `playbooks/{create,destroy,prepare}.yml` dispatchers, which read `mp_backend` from the molecule group's hostvars.

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Computes per-host merged specs, creates user-defined podman networks, then creates containers from `hostvars[item].mp.podman.*` for each host in `groups['molecule']`. Writes `ansible_connection: containers.podman.podman` per host into the runtime inventory file. |
| `destroy` | Removes those containers and any non-reserved networks. |
| `prepare` | Installs `sudo` inside each container. |

## Inputs (per-host, in inventory)

```yaml
all:
  children:
    molecule:
      hosts:
        instance:
          mp:
            podman:
              image: docker.io/...:tag         # required
              command: /sbin/init              # optional, role default '/sbin/init'
              privileged: false                # optional, role default false
              volumes: []                      # optional
              capabilities: []                 # optional
              podman_network: []               # optional, list or single string
              env: {}                          # optional
              tmpfs: []                        # optional
              exposed_ports: []                # optional
              published_ports: []              # optional
              systemd: always               # optional — 'always' | 'true' | 'false' | leave unset
              cgroupns: host                # optional — 'host' | 'private'
```

Shared defaults can be hoisted into `mp_defaults.podman` in `inventory/group_vars/molecule.yml` (overrides role defaults; per-host fields override mp_defaults). Field resolution order in the role: role defaults <- `mp_defaults.podman` <- `hostvars[item].mp.podman`.

## Role-level overrides

See `defaults/main.yml` (`mp_podman_role_defaults`, `mp_podman_async_*`, `mp_podman_reserved_networks`).
