.. _ansible_collections.david_igou.molecule_provisioners.docsite.guide_getting_started:

Getting started
===============

`Molecule <https://docs.ansible.com/projects/molecule/>`__ tests Ansible content
by creating throwaway machines, running your content against them, checking the
result, and deleting the machines. This collection does the creating and
deleting: you list the machines you want in an Ansible inventory, and it
provisions them as containers or virtual machines.

In this guide you add a Molecule scenario to an Ansible collection. The scenario
starts one Podman container, creates a user in it, checks that the user exists,
and removes the container. It takes about ten minutes.

What you need
-------------

- A Linux or macOS machine with Python 3.11 or newer.
- `Podman <https://podman.io/docs/installation>`__, working for your user
  (``podman run --rm docker.io/library/alpine true`` succeeds).
- ansible-core 2.19 or newer and Molecule 26 or newer (this guide was tested
  with Molecule 26.11):

  .. code-block:: console

     $ python3 -m pip install 'ansible-core>=2.19' 'molecule>=26'

- An Ansible collection to test, that is, a directory with a ``galaxy.yml``.
  To follow along without one, create an empty collection:

  .. code-block:: console

     $ ansible-galaxy collection init my_namespace.my_collection
     $ cd my_namespace/my_collection

Every path and command below is relative to the collection's root directory,
the one that contains ``galaxy.yml``.

Step 1: Look at the files you will create
-----------------------------------------

.. code-block:: text

   my_collection/
   ├── galaxy.yml                      # already exists
   └── extensions/
       └── molecule/
           ├── config.yml              # settings shared by every scenario
           ├── requirements-test.yml   # pins this collection
           └── default/                # the scenario
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

Create the directories in one go:

.. code-block:: console

   $ mkdir -p extensions/molecule/default/inventory/group_vars

Step 2: Pin this collection
---------------------------

Molecule installs the collections a scenario needs before it runs. Pin this
collection to an exact version so a new release cannot change your tests
without a change in your repository.

.. code-block:: yaml

   # extensions/molecule/requirements-test.yml
   ---
   collections:
     - name: david_igou.molecule_provisioners
       version: 0.0.8-alpha

Step 3: Share the Molecule settings
-----------------------------------

``config.yml`` holds the settings every scenario uses, so each scenario's own
``molecule.yml`` only needs a name. Molecule merges it into every scenario when
you run Molecule from the collection root, as in step 7.

.. code-block:: yaml

   # extensions/molecule/config.yml
   ---
   dependency:
     name: galaxy
     options:
       requirements-file: extensions/molecule/requirements-test.yml
       role-file: extensions/molecule/requirements-test.yml

   ansible:
     executor:
       args:
         ansible_playbook:
           - --inventory=inventory/
           - --inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/
     playbooks:
       create: create.yml
       destroy: destroy.yml
       prepare: prepare.yml
       converge: converge.yml
       verify: verify.yml

   scenario:
     destroy_sequence:
       - dependency
       - destroy
     test_sequence:
       - dependency
       - syntax
       - create
       - prepare
       - converge
       - idempotence
       - verify
       - destroy

   verifier:
     name: ansible

The two ``--inventory`` lines matter: the first is the scenario's own inventory
(step 5), and the second is where the collection writes connection details for
the machines it creates.

``requirements-file`` and ``role-file`` name the same file because Molecule
reads collections from the first and roles from the second, and warns on every
run when the roles file is missing. ``destroy_sequence`` leaves out the
optional ``cleanup`` step, so ``molecule destroy`` does not warn about a
missing ``cleanup.yml``.

Step 4: Name the scenario and import the lifecycle playbooks
------------------------------------------------------------

.. code-block:: yaml

   # extensions/molecule/default/molecule.yml
   ---
   scenario:
     name: default

Molecule runs ``create.yml``, ``prepare.yml`` and ``destroy.yml`` at those steps.
Each one imports the matching playbook from this collection:

.. code-block:: yaml

   # extensions/molecule/default/create.yml
   ---
   - name: Create instances
     import_playbook: david_igou.molecule_provisioners.create

.. code-block:: yaml

   # extensions/molecule/default/prepare.yml
   ---
   - name: Prepare instances
     import_playbook: david_igou.molecule_provisioners.prepare

.. code-block:: yaml

   # extensions/molecule/default/destroy.yml
   ---
   - name: Destroy instances
     import_playbook: david_igou.molecule_provisioners.destroy

Step 5: Describe the instances
------------------------------

The instances are the hosts of the ``molecule`` inventory group. This
scenario has one, called ``instance``:

.. code-block:: yaml

   # extensions/molecule/default/inventory/hosts.yml
   ---
   all:
     children:
       molecule:
         hosts:
           instance: {}

``mp_backend`` chooses how the instances are created, and ``mp_defaults``
holds settings for every instance. With the ``podman`` backend these are
options of the
`podman_container <https://docs.ansible.com/ansible/latest/collections/containers/podman/podman_container_module.html>`__
module; this image already has Python, which Ansible needs inside the
container.

.. code-block:: yaml

   # extensions/molecule/default/inventory/group_vars/molecule.yml
   ---
   mp_backend: podman
   mp_defaults:
     podman:
       image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest

Step 6: Write the test
----------------------

``converge.yml`` applies the content you are testing. Here it creates a user.
In your collection it would usually run one of your roles instead:

.. code-block:: yaml

   # extensions/molecule/default/converge.yml, testing a role instead
   ---
   - name: Converge
     hosts: molecule
     tasks:
       - name: Apply the role under test
         ansible.builtin.include_role:
           name: my_namespace.my_collection.my_role

For this guide, use the version that creates a user:

.. code-block:: yaml

   # extensions/molecule/default/converge.yml
   ---
   - name: Converge
     hosts: molecule
     tasks:
       - name: Create the deploy user
         ansible.builtin.user:
           name: deploy

``verify.yml`` checks the result. ``ansible.builtin.getent`` fails when the
user does not exist:

.. code-block:: yaml

   # extensions/molecule/default/verify.yml
   ---
   - name: Verify
     hosts: molecule
     gather_facts: false
     tasks:
       - name: Look up the deploy user
         ansible.builtin.getent:
           database: passwd
           key: deploy

Step 7: Run it
--------------

From the collection root:

.. code-block:: console

   $ MOLECULE_GLOB='extensions/molecule/*/molecule.yml' molecule test -s default

``MOLECULE_GLOB`` tells Molecule where the scenarios are, which is also how it
finds ``config.yml``. Molecule then runs these steps and reports each one:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Step
     - What happens
   * - dependency
     - Installs the collections in ``requirements-test.yml`` and the collections
       they depend on into ``~/.ansible/collections``. The first run downloads
       the collections for every backend, so it takes a minute.
   * - syntax
     - Checks the playbooks' syntax.
   * - create
     - Starts a container named ``instance`` and records how to reach it.
   * - prepare
     - Waits until Ansible can run commands in the container.
   * - converge
     - Runs ``converge.yml``.
   * - idempotence
     - Runs ``converge.yml`` again and fails if anything changed the second time.
   * - verify
     - Runs ``verify.yml``.
   * - destroy
     - Removes the container.

The run ends with a recap that lists each step as ``Executed: Successful``,
followed by a totals line ending in ``failed=0``. If a step fails, Molecule
still destroys the container.

Working on a scenario
---------------------

``molecule test`` always starts and ends with nothing running. While you write
a test, run the steps on their own instead:

.. code-block:: console

   $ export MOLECULE_GLOB='extensions/molecule/*/molecule.yml'
   $ molecule converge -s default    # creates the container if needed, then converges
   $ molecule verify -s default
   $ podman exec -it instance bash   # look around inside the container
   $ molecule destroy -s default

Next steps
----------

- Add more hosts to ``inventory/hosts.yml``, and give one host its own settings
  under ``mp.podman``; see
  :ref:`ansible_collections.david_igou.molecule_provisioners.docsite.guide_scenarios`.
- Switch ``mp_backend`` to
  :ref:`docker <ansible_collections.david_igou.molecule_provisioners.docsite.backend_docker>`,
  or test on virtual machines with
  :ref:`qemu <ansible_collections.david_igou.molecule_provisioners.docsite.backend_qemu>`,
  :ref:`kubevirt <ansible_collections.david_igou.molecule_provisioners.docsite.backend_kubevirt>` or
  :ref:`vmware <ansible_collections.david_igou.molecule_provisioners.docsite.backend_vmware>`.
  Each backend guide lists what it needs.
- Moving an existing ``platforms:`` scenario? See
  :ref:`ansible_collections.david_igou.molecule_provisioners.docsite.guide_migration`.
