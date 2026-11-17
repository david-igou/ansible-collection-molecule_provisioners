.. _ansible_collections.david_igou.molecule_provisioners.docsite.guide_scenarios:

Writing scenarios
=================

A scenario lists its instances in inventory and imports three playbooks from
this collection for ``create``, ``prepare`` and ``destroy``. Switching
backends is one variable.

Layout
------

A collection keeps one pinned dependency and one shared Molecule config, then a
small directory per scenario. Copy the files from
`docs/examples/ <https://github.com/david-igou/ansible-collection-molecule_provisioners/tree/main/docs/examples>`__.

.. code-block:: text

   extensions/molecule/
   ├── config.yml               # shared Molecule settings and the pinned dependency
   ├── requirements-test.yml    # david_igou.molecule_provisioners, exact version
   └── default/
       ├── molecule.yml         # scenario name only
       ├── create.yml
       ├── prepare.yml
       ├── destroy.yml
       ├── converge.yml
       ├── verify.yml
       └── inventory/
           ├── hosts.yml
           └── group_vars/molecule.yml

``create.yml``, ``prepare.yml`` and ``destroy.yml`` each import the matching
playbook:

.. code-block:: yaml

   ---
   - name: Create instances
     import_playbook: david_igou.molecule_provisioners.create

Plays that set up scenario-specific resources, such as a Secret a VM needs at
first boot, can run before or after the import.

``requirements-test.yml`` pins an exact release so provisioning cannot change
between runs without a change in your repository. ``config.yml`` points the
Molecule ``dependency`` step at it and passes both inventories to every
playbook:

.. code-block:: yaml

   ansible:
     executor:
       args:
         ansible_playbook:
           - --inventory=inventory/
           - --inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/

Molecule only merges ``extensions/molecule/config.yml`` when it runs from the
collection root with ``MOLECULE_GLOB=extensions/molecule/*/molecule.yml``; the
example ``Makefile`` sets it.

Instances
---------

Instances are the hosts of the ``molecule`` group. ``mp_backend`` picks the
backend, ``mp_defaults.<backend>`` holds settings shared by every host, and a
host's own ``mp.<backend>`` overrides them:

.. code-block:: yaml

   # inventory/group_vars/molecule.yml
   mp_backend: "{{ lookup('ansible.builtin.env', 'PROVISIONER') | default('podman', true) }}"
   mp_defaults:
     podman:
       image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
     qemu:
       image: https://cloud-images.ubuntu.com/releases/noble/release-20260518/ubuntu-24.04-server-cloudimg-amd64.img
       ssh_user: ubuntu
     kubevirt:
       boot_source:
         type: container_disk
         image: quay.io/containerdisks/ubuntu:24.04
       ssh_user: ubuntu

.. code-block:: yaml

   # inventory/hosts.yml
   all:
     children:
       molecule:
         hosts:
           server: {}
           client:
             mp:
               qemu:
                 memory: 2048

``PROVISIONER=qemu molecule test`` then runs the same scenario on VMs. A host
needs no ``mp`` block when ``mp_defaults`` covers it.

Each backend merges its own instance defaults, then ``mp_defaults.<backend>``,
then ``mp.<backend>``, recursively. The container backends pass the result to
:literal:`containers.podman.podman_container` or
:literal:`community.docker.docker_container`, so any option of those modules
works. The VM backends validate it against their ``instance`` settings and
reject keys they do not know. Role variables such as
``mp_kubevirt_wait_timeout`` can live in any inventory scope that covers the
molecule hosts.

How a run works
---------------

``create`` and ``destroy`` are plays on ``hosts: molecule`` that run every task
on the controller, so each host reads its own inventory variables and hosts are
provisioned in parallel, up to the fork limit. Create names shared resources
(VMs, Services, processes) after the host and a run id kept in the Molecule
ephemeral directory, so concurrent runs do not collide and destroy finds what
create made.

Create then writes each host's connection details (container connection, or
SSH address, port and key) to
``$MOLECULE_EPHEMERAL_DIRECTORY/inventory/molecule_runtime.yml``. The second
``--inventory`` argument loads that file for every later phase. ``prepare``
runs against the instances and waits until each accepts an Ansible
connection.

Migrating from ``platforms:`` scenarios or older releases is covered in
:ref:`ansible_collections.david_igou.molecule_provisioners.docsite.guide_migration`.
