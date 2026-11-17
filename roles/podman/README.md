# david_igou.molecule_provisioners.podman

Runs each molecule host as a Podman container named after the host. Molecule
then reaches it through the `containers.podman.podman` connection as `root`
(set `ansible_user` on the host to use another account).

## Instance settings

`mp.podman` is passed straight to
[`containers.podman.podman_container`](https://docs.ansible.com/ansible/latest/collections/containers/podman/podman_container_module.html),
so any option of that module works. The role merges, in order:

1. `mp_podman_instance_defaults`: `command: sleep infinity`
2. `mp_defaults.podman` from `group_vars/molecule.yml`
3. the host's own `mp.podman`

```yaml
# inventory/group_vars/molecule.yml
mp_backend: podman
mp_defaults:
  podman:
    image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
    network:
      - molecule
mp_podman_networks:
  - name: molecule
    subnet: 10.89.0.0/24

# inventory/hosts.yml
all:
  children:
    molecule:
      hosts:
        web: {}
        systemd-host:
          mp:
            podman:
              command: /sbin/init
              systemd: always
```

Set `command: null` to keep the image's own command. Create fails if a
container exits within two seconds of starting, and points you at
`podman logs`.

## Role variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `mp_podman_networks` | `[]` | `containers.podman.podman_network` arguments, one dict per network; created first, removed last |
| `mp_podman_instance_defaults` | see above | Lowest settings layer |
| `mp_podman_executable` | `$MOLECULE_PODMAN_EXECUTABLE` or `podman` | Podman binary for the modules and the connection |
