# `david_igou.molecule_provisioners.docker`

Molecule provisioner role for docker containers. Use the collection's `playbooks/{create,destroy,prepare}.yml` dispatchers to invoke this role. They select the backend using `mp_backend` from the molecule group's hostvars.

## Entry points

| `tasks_from` | What it does |
| --- | --- |
| `create` | Computes per-host merged specs, creates user-defined docker networks, then creates containers from `hostvars[item].mp.docker.*` for each host in `groups['molecule']`. Writes `ansible_connection: community.docker.docker` per host into the runtime inventory file. |
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
            docker:
              image: docker.io/...:tag         # required
              command: /sbin/init              # optional; when unset (and override_command is true) the role uses a long-lived keepalive
              command_handling: compatibility  # optional, role default 'compatibility'
              override_command: true           # optional, role default true
              hostname: <str>                  # optional, defaults to inventory_hostname
              privileged: false                # optional, role default false
              user: <str>                      # optional
              tty: <bool>                      # optional
              pid_mode: <str>                  # optional
              cgroupns_mode: <str>             # optional
              runtime: <str>                   # optional
              platform: <str>                  # optional
              capabilities: []                 # optional
              security_opts:                  # optional
                - <str>
              sysctls:                        # optional
                <k>: <v>
              ulimits:                        # optional
                - <str>
              devices:                        # optional
                - <str>
              volumes: []                      # optional
              mounts:                         # optional, list of mount mappings
                - type: bind
                  source: /path/on/controller
                  target: /path/in/container
              tmpfs:                          # optional
                - <str>
              shm_size: <str>                  # optional
              networks:                       # optional; role creates/deletes the network
                - name: <str>
              network_mode: <str>              # optional
              networks_cli_compatible: true    # optional, role default true
              purge_networks: <bool>           # optional
              dns_servers:                    # optional
                - <str>
              etc_hosts:                      # optional
                <host>: <ip>
              exposed_ports:                  # optional
                - <str>
              published_ports:                # optional
                - <str>
              links:                          # optional
                - <str>
              env: {}                          # optional
              labels: {}                       # optional
              restart_policy: <str>            # optional
              restart_retries: <int>           # optional
              stop_signal: <str>               # optional
              kill_signal: <str>               # optional
              memory: <str>                    # optional
              memory_swap: <str>               # optional
              # destroy-time
              force_kill: true                 # optional, role default true
              keep_volumes: true               # optional, role default true
```

Set shared defaults in `mp_defaults.docker` in `inventory/group_vars/molecule.yml`. Field resolution order: role defaults <- `mp_defaults.docker` <- `hostvars[item].mp.docker`.

## Connection user

The role writes `ansible_user: root` into the runtime inventory by default, matching the typical container default. Override per-host by setting `ansible_user` in your static inventory (in `inventory/hosts.yml`, or in `group_vars/molecule.yml`); the role reads it from hostvars before writing the runtime inventory, so consumer-set values win.

Note that `mp.docker.user` is a different setting — it controls the container process owner (passed to `community.docker.docker_container`'s `user`), not the user ansible operates as inside the container.

## Role-level overrides

See `defaults/main.yml` (`mp_docker_role_defaults`, `mp_docker_async_*`, `mp_docker_reserved_networks`).

## Prerequisites

- A reachable local docker daemon.
- The `community.docker` collection (declared as a dependency in this collection's `galaxy.yml`).
- The `docker` python package on the controller (`pip install docker`).

## Out of scope

- Image build at create time (`Dockerfile.j2`, `pre_build_image`, `buildargs`, `cache_from`). Ship a fully-prepared image.
- Private registry login (`docker_login`). Bake credentials into the docker daemon config or pre-pull the image.
- Remote docker daemons / TLS (`docker_host`, `cacert_path`, `cert_path`, `key_path`, `tls_verify`). Local socket only.
