.. _ansible_collections.david_igou.molecule_provisioners.docsite.backend_podman:

Podman backend
==============

Runs each molecule host as a Podman container named after the host. Molecule
then reaches it through the ``containers.podman.podman`` connection as ``root``
(set ``ansible_user`` on the host to use another account).

Every option of the ``create``, ``destroy`` and ``prepare`` entry points is listed in the :ref:`role reference <ansible_collections.david_igou.molecule_provisioners.podman_role>`.

Requirements
------------

- Podman, working for the user that runs Molecule (rootless is fine).
- Images that contain Python, which Ansible needs inside the container.

The ``containers.podman`` collection is installed by Molecule's ``dependency``
step together with this collection.

Instance settings
-----------------

``mp.podman`` is passed straight to
`containers.podman.podman_container <https://docs.ansible.com/ansible/latest/collections/containers/podman/podman_container_module.html>`__,
so any option of that module works. The role merges, in order:

1. ``mp_podman_instance_defaults``: ``command: sleep infinity`` and ``hostname`` set
   to the inventory name
2. ``mp_defaults.podman`` from ``group_vars/molecule.yml``
3. the host's own ``mp.podman``

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
   mp_backend: podman
   mp_defaults:
     podman:
       image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
       network:
         - molecule
   mp_podman_networks:
     - name: molecule
       subnet: 10.89.0.0/24

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
               podman:
                 command: /sbin/init
                 systemd: always

Set ``command: null`` to keep the image's own command. Create fails if a
container exits within two seconds of starting, and points you at
``podman logs``.
