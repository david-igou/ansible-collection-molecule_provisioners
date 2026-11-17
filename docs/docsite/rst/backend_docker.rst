.. _ansible_collections.david_igou.molecule_provisioners.docsite.backend_docker:

Docker backend
==============

Runs each molecule host as a Docker container named after the host. Molecule
then reaches it through the ``community.docker.docker`` connection as ``root``
(set ``ansible_user`` on the host to use another account).

Every option of the ``create``, ``destroy`` and ``prepare`` entry points is listed in the :ref:`role reference <ansible_collections.david_igou.molecule_provisioners.docker_role>`.

Requirements
------------

- A Docker daemon the user that runs Molecule can reach (``docker ps`` works;
  ``DOCKER_HOST`` is honored).
- The ``requests`` Python package on the machine that runs Molecule.
- Images that contain Python, which Ansible needs inside the container.

The ``community.docker`` collection is installed by Molecule's ``dependency``
step together with this collection.

Instance settings
-----------------

``mp.docker`` is passed straight to
`community.docker.docker_container <https://docs.ansible.com/ansible/latest/collections/community/docker/docker_container_module.html>`__,
so any option of that module works. The role merges, in order:

1. ``mp_docker_instance_defaults``: ``command: sleep infinity`` and ``hostname`` set
   to the inventory name
2. ``mp_defaults.docker`` from ``group_vars/molecule.yml``
3. the host's own ``mp.docker``

These files belong to a scenario set up as in
:ref:`ansible_collections.david_igou.molecule_provisioners.docsite.guide_getting_started`;
only the inventory differs from that guide:

.. code-block:: text

   extensions/molecule/default/
   ├── molecule.yml
   ├── create.yml
   ├── prepare.yml
   ├── destroy.yml
   ├── converge.yml
   ├── verify.yml
   └── inventory/
       ├── hosts.yml                 # the instances
       └── group_vars/
           └── molecule.yml          # backend choice and shared settings

.. code:: yaml

   # extensions/molecule/default/inventory/group_vars/molecule.yml
   ---
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

.. code:: yaml

   # extensions/molecule/default/inventory/hosts.yml
   ---
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

Set ``command: null`` to keep the image's own command. Create fails if a
container exits within two seconds of starting, and points you at
``docker logs``. 