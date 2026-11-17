# david_igou.molecule_provisioners.docker

Runs each molecule host as a Docker container named after the host. Molecule
then reaches it through the `community.docker.docker` connection as `root`
(set `ansible_user` on the host to use another account).

## Instance settings

`mp.docker` is passed straight to
[`community.docker.docker_container`](https://docs.ansible.com/ansible/latest/collections/community/docker/docker_container_module.html),
so any option of that module works. The role merges, in order:

1. `mp_docker_instance_defaults`: `command: sleep infinity`
2. `mp_defaults.docker` from `group_vars/molecule.yml`
3. the host's own `mp.docker`

```yaml
# inventory/group_vars/molecule.yml
mp_backend: docker
mp_defaults:
  docker:
    image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
    networks:
      - name: molecule
mp_docker_networks:
  - name: molecule
    ipam_config:
      - subnet: 10.89.0.0/24

# inventory/hosts.yml
all:
  children:
    molecule:
      hosts:
        web: {}
        systemd-host:
          mp:
            docker:
              command: /sbin/init
              privileged: true
              cgroupns_mode: host
              volumes:
                - /sys/fs/cgroup:/sys/fs/cgroup:rw
```

Set `command: null` to keep the image's own command. Create fails if a
container exits within two seconds of starting, and points you at
`docker logs`. The Docker daemon is found the way the community.docker modules
find it (`DOCKER_HOST` and related variables).

## Role variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `mp_docker_networks` | `[]` | `community.docker.docker_network` arguments, one dict per network; created first, removed last |
| `mp_docker_instance_defaults` | see above | Lowest settings layer |
