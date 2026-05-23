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
              podman_network: []               # optional, str | list[str] | list[{name, subnet?, gateway?}]
              env: {}                          # optional
              tmpfs: []                        # optional
              exposed_ports: []                # optional
              published_ports: []              # optional
              systemd: always                  # optional — 'always' | 'true' | 'false'
              cgroupns: host                   # optional — 'host' | 'private'
              hostname: <str>                  # optional
              tty: true                        # optional
              detach: true                     # optional (default behavior)
              etc_hosts: {}                    # optional — dict of host:ip
              dns_servers: []                  # optional — list of DNS server IPs
              pid_mode: <str>                  # optional — 'host', 'container:<id>', 'private'
              security_opts: []                # optional — e.g. ['seccomp=unconfined']
              devices: []                      # optional — list of '/host:/ctr[:rwm]' mappings
              ulimits: []                      # optional — e.g. ['nofile=1024:2048']
              ip: <str>                        # optional — only with a network that has a subnet
              restart_policy: <str>            # optional — 'no', 'on-failure', 'always', 'unless-stopped'
              restart_retries: <int>           # optional — paired with restart_policy=on-failure
              cgroup_manager: <str>            # optional — 'systemd' | 'cgroupfs' (CLI flag)
              storage_opt: []                  # optional — list of '--storage-opt=' values
              storage_driver: <str>            # optional — '--storage-driver=' value
              extra_opts: []                   # optional — raw `podman` CLI flags appended last
```

### Network shape

`podman_network` accepts three shapes:

- A single string: `podman_network: my-net` → joins a pre-existing network named `my-net`.
- A list of strings: `podman_network: [a, b]` → joins both networks (creates them if missing).
- A list of dicts: `podman_network: [{name: my-net, subnet: 10.89.0.0/24, gateway: 10.89.0.1}]` → creates `my-net` with the given subnet on first apply, then joins.

Names listed in `mp_podman_reserved_networks` (default: `bridge`, `none`, `host`, `slirp4netns`, `ns`, `private`) and pseudo-references prefixed with `ns:` or `container:` are skipped during network create/destroy.

Shared defaults can be hoisted into `mp_defaults.podman` in `inventory/group_vars/molecule.yml` (overrides role defaults; per-host fields override mp_defaults). Field resolution order in the role: role defaults <- `mp_defaults.podman` <- `hostvars[item].mp.podman`.

## Role-level overrides

See `defaults/main.yml` (`mp_podman_role_defaults`, `mp_podman_async_*`, `mp_podman_reserved_networks`).
