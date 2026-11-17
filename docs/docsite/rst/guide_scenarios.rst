.. _ansible_collections.david_igou.molecule_provisioners.docsite.guide_scenarios:

Writing scenarios
=================

This guide builds on
:ref:`ansible_collections.david_igou.molecule_provisioners.docsite.guide_getting_started`:
how settings are layered, how one scenario runs on several backends, how to
add your own plays, and how to use the collection outside a collection
repository.

Layout with several scenarios
-----------------------------

Every scenario is a directory next to the shared ``config.yml`` and
``requirements-test.yml``:

.. code-block:: text

   my_collection/
   ├── galaxy.yml
   └── extensions/
       └── molecule/
           ├── config.yml
           ├── requirements-test.yml
           ├── default/
           │   ├── molecule.yml          # scenario name: default
           │   ├── create.yml
           │   ├── prepare.yml
           │   ├── destroy.yml
           │   ├── converge.yml
           │   ├── verify.yml
           │   └── inventory/
           │       ├── hosts.yml
           │       └── group_vars/
           │           └── molecule.yml
           └── cluster/
               ├── molecule.yml          # scenario name: cluster
               └── ...                   # the same files, with their own inventory

``molecule test --all`` runs every scenario; ``-s cluster`` runs one. Run
Molecule from the collection root with
``MOLECULE_GLOB='extensions/molecule/*/molecule.yml'`` so it finds
``config.yml``. A ``Makefile`` that sets it saves typing; the repository has
one in
`docs/examples/ <https://github.com/david-igou/ansible-collection-molecule_provisioners/tree/main/docs/examples>`__,
together with every file from the Getting started guide.

Settings for each instance
--------------------------

Every host in the ``molecule`` group is an instance. Its settings are merged
from three layers, later layers winning:

1. the backend's built-in defaults (for example, Podman containers run
   ``sleep infinity`` unless you set a ``command``);
2. ``mp_defaults.<backend>``, usually in ``inventory/group_vars/molecule.yml``,
   for settings every instance shares;
3. ``mp.<backend>`` on the host, in ``inventory/hosts.yml``, for settings of
   that instance only.

Dictionaries merge key by key; lists are replaced. In this scenario both
containers use the same image, ``server`` publishes a port, and ``client``
gets an environment variable:

.. code-block:: text

   extensions/molecule/default/inventory/
   ├── hosts.yml
   └── group_vars/
       └── molecule.yml

.. code-block:: yaml

   # extensions/molecule/default/inventory/group_vars/molecule.yml
   ---
   mp_backend: podman
   mp_defaults:
     podman:
       image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest

.. code-block:: yaml

   # extensions/molecule/default/inventory/hosts.yml
   ---
   all:
     children:
       molecule:
         hosts:
           server:
             mp:
               podman:
                 publish:
                   - 8080:80
           client:
             mp:
               podman:
                 env:
                   ROLE: client

What you may set depends on the backend:

- ``podman`` and ``docker`` pass the merged settings to
  ``containers.podman.podman_container`` and
  ``community.docker.docker_container``, so any option of those modules works.
- ``qemu``, ``kubevirt`` and ``vmware`` check the merged settings against
  their own list and stop with an error naming any key they do not know. The
  backend guides and role reference pages list them.

Hosts can belong to other inventory groups as usual, for example a
``webservers`` group that your playbook targets. Only membership in
``molecule`` makes a host an instance.

Running one scenario on several backends
----------------------------------------

Give each instance settings for every backend you want, and choose the backend
when you run Molecule. Here ``PROVISIONER`` picks the backend and Podman is the
default:

.. code-block:: yaml

   # extensions/molecule/default/inventory/group_vars/molecule.yml
   ---
   mp_backend: "{{ lookup('ansible.builtin.env', 'PROVISIONER') | default('podman', true) }}"
   mp_defaults:
     podman:
       image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
     qemu:
       image: https://cloud-images.ubuntu.com/releases/noble/release-20260518/ubuntu-24.04-server-cloudimg-amd64.img
       image_checksum: sha256:53fdde898feed8b027d94baa9cfe8229867f330a1d9c49dc7d84465ee7f229f7
       ssh_user: ubuntu

.. code-block:: console

   $ export MOLECULE_GLOB='extensions/molecule/*/molecule.yml'
   $ PROVISIONER=qemu molecule test -s default

Containers and virtual machines differ in what they can test. A container has
no kernel or boot process of its own and usually no systemd, so content that
manages those needs a virtual machine.

Adding your own plays
---------------------

``create.yml``, ``prepare.yml`` and ``destroy.yml`` are ordinary playbooks, so
a scenario can run its own plays around the import. For example, a virtual
machine that reads a Kubernetes Secret on first boot needs the Secret before
create and can lose it after destroy:

.. code-block:: text

   extensions/molecule/default/
   ├── create.yml        # imports secret.yml, then the collection's create
   ├── destroy.yml       # imports the collection's destroy, then secret.yml
   ├── secret.yml        # the scenario's own play
   └── ...

.. code-block:: yaml

   # extensions/molecule/default/secret.yml
   ---
   - name: Manage the scenario's Secret
     hosts: localhost
     gather_facts: false
     tasks:
       - name: Create or remove the Secret
         kubernetes.core.k8s:
           state: "{{ secret_state | default('present') }}"
           definition:
             apiVersion: v1
             kind: Secret
             metadata:
               name: my-scenario-secret
               namespace: molecule
             stringData:
               token: example

.. code-block:: yaml

   # extensions/molecule/default/create.yml
   ---
   - name: Create the scenario's Secret
     import_playbook: secret.yml

   - name: Create instances
     import_playbook: david_igou.molecule_provisioners.create

.. code-block:: yaml

   # extensions/molecule/default/destroy.yml
   ---
   - name: Destroy instances
     import_playbook: david_igou.molecule_provisioners.destroy

   - name: Remove the scenario's Secret
     import_playbook: secret.yml
     vars:
       secret_state: absent

Plays in ``prepare.yml`` placed after the import run once every instance
accepts connections, which suits installing test fixtures.

Roles and playbook repositories
-------------------------------

Outside a collection, Molecule looks for scenarios in ``molecule/`` and reads
shared settings from ``.config/molecule/config.yml`` at the root of the Git
repository. Use the ``config.yml`` from step 3 of Getting started there, with
``requirements-file`` and ``role-file`` pointing at ``molecule/requirements.yml``:

.. code-block:: text

   my_role/
   ├── .config/
   │   └── molecule/
   │       └── config.yml          # step 3's config.yml
   ├── tasks/
   │   └── main.yml
   └── molecule/
       ├── requirements.yml        # step 2's requirements-test.yml
       └── default/
           ├── molecule.yml
           ├── create.yml
           ├── prepare.yml
           ├── destroy.yml
           ├── converge.yml
           ├── verify.yml
           └── inventory/
               ├── hosts.yml
               └── group_vars/
                   └── molecule.yml

.. code-block:: yaml

   # .config/molecule/config.yml (the lines that differ from step 3)
   dependency:
     name: galaxy
     options:
       requirements-file: molecule/requirements.yml
       role-file: molecule/requirements.yml

Run ``molecule test`` from the repository root; ``MOLECULE_GLOB`` is not needed
because ``molecule/*/molecule.yml`` is where Molecule looks by default.

How a run works
---------------

``create`` and ``destroy`` run on your machine, once for each instance and in
parallel. Each instance reads its own inventory variables, so ``mp``,
``mp_defaults`` and backend settings such as ``mp_kubevirt_wait_timeout`` can
live in any inventory file that applies to the ``molecule`` hosts.

Create names what it makes after the instance and a short run id stored in
``$MOLECULE_EPHEMERAL_DIRECTORY``, the temporary directory Molecule keeps for
each scenario, so two runs at the same time do not collide and
destroy removes exactly what create made. It then writes each instance's
connection details (the container connection, or an SSH address, port and key)
to ``$MOLECULE_EPHEMERAL_DIRECTORY/inventory/molecule_runtime.yml``. The second
``--inventory`` argument in ``config.yml`` loads that file, so ``prepare``,
``converge`` and ``verify`` reach the instances without further settings.
