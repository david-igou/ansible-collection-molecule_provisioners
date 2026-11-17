.. _ansible_collections.david_igou.molecule_provisioners.docsite.guide_migration:

Migrating from platforms and drivers
====================================

Molecule scenarios written for ``molecule-plugins`` list their instances under
``platforms:`` and pick a ``driver:`` such as ``podman`` or ``docker``. This
guide moves such a scenario to this collection: the instances move into an
Ansible inventory, and Molecule's ``create``, ``prepare`` and ``destroy`` steps
import playbooks from this collection. Your ``converge.yml`` and ``verify.yml``
do not change.

The guide uses a collection with one scenario named ``default``. If you have
never run a scenario with this collection, work through
:ref:`ansible_collections.david_igou.molecule_provisioners.docsite.guide_getting_started`
first.

Before
------

A typical ``molecule-plugins`` scenario in a collection:

.. code-block:: text

   my_collection/
   ├── galaxy.yml
   └── extensions/
       └── molecule/
           └── default/
               ├── molecule.yml
               ├── converge.yml
               └── verify.yml

.. code-block:: yaml

   # extensions/molecule/default/molecule.yml
   dependency:
     name: galaxy
   driver:
     name: podman
   platforms:
     - name: web
       image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
       command: /sbin/init
       privileged: true
       pre_build_image: true
       groups:
         - webservers
     - name: db
       image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
       command: /sbin/init
       privileged: true
       pre_build_image: true
       env:
         ROLE: database
   provisioner:
     name: ansible
   verifier:
     name: ansible

After
-----

.. code-block:: text

   my_collection/
   ├── galaxy.yml
   └── extensions/
       └── molecule/
           ├── config.yml                  # new: settings shared by every scenario
           ├── requirements-test.yml       # new: pins this collection
           └── default/
               ├── molecule.yml            # now only the scenario name
               ├── create.yml              # new
               ├── prepare.yml             # new
               ├── destroy.yml             # new
               ├── converge.yml            # unchanged
               ├── verify.yml              # unchanged
               └── inventory/              # new: replaces platforms:
                   ├── hosts.yml
                   └── group_vars/
                       └── molecule.yml

Steps
-----

1. **Add the shared files.** Create ``extensions/molecule/config.yml`` and
   ``extensions/molecule/requirements-test.yml`` exactly as shown in
   :ref:`steps 2 and 3 of Getting started <ansible_collections.david_igou.molecule_provisioners.docsite.guide_getting_started>`.
   ``config.yml`` replaces the ``dependency``, ``provisioner`` and ``verifier``
   sections that every old ``molecule.yml`` repeated.

2. **Shrink molecule.yml.** Keep only the scenario name. If the old file set
   other scenario-specific options (``provisioner.env``, ``config_options``, a
   custom ``test_sequence``), keep those too; Molecule applies them on top of
   ``config.yml``. Move ``provisioner.inventory`` host and group variables into
   ``inventory/host_vars/`` and ``inventory/group_vars/``.

   .. code-block:: yaml

      # extensions/molecule/default/molecule.yml
      ---
      scenario:
        name: default

3. **Add the lifecycle playbooks.** Each one imports the matching playbook from
   this collection. If the scenario had its own ``create.yml``,
   ``prepare.yml`` or ``destroy.yml``, keep any scenario-specific plays and
   put them before or after the import.

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

4. **Move the platforms into inventory.** Each platform becomes a host of the
   ``molecule`` group, named after the platform's ``name``. Settings that every
   platform shared go in ``mp_defaults.<backend>``; settings that differ go on
   the host under ``mp.<backend>``. The backend is the old driver name.

   .. code-block:: yaml

      # extensions/molecule/default/inventory/group_vars/molecule.yml
      ---
      mp_backend: podman
      mp_defaults:
        podman:
          image: docker.io/geerlingguy/docker-ubuntu2404-ansible:latest
          command: /sbin/init
          privileged: true

   .. code-block:: yaml

      # extensions/molecule/default/inventory/hosts.yml
      ---
      all:
        children:
          molecule:
            hosts:
              web: {}
              db:
                mp:
                  podman:
                    env:
                      ROLE: database
          webservers:          # was "groups: [webservers]" on the web platform
            hosts:
              web: {}

   A host listed under another group, like ``webservers`` here, is the same
   host; Ansible merges the two entries.

5. **Run it** from the collection root:

   .. code-block:: console

      $ MOLECULE_GLOB='extensions/molecule/*/molecule.yml' molecule test -s default

   Molecule installs the pinned collection, creates the containers, runs your
   unchanged ``converge.yml`` and ``verify.yml``, and removes the containers.

6. **Drop the old driver.** Remove ``molecule-plugins`` (and extras such as
   ``molecule-plugins[podman]``) from your Python requirements.

Platform keys
-------------

With the ``podman`` and ``docker`` backends, ``mp.podman`` and ``mp.docker`` are
passed to ``containers.podman.podman_container`` and
``community.docker.docker_container``. Those modules accept most old platform
keys under the same names or as aliases (``volumes``, ``published_ports``,
``env`` and so on). Each container's hostname is its inventory name, as before.
The exceptions:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Platform key
     - What to do
   * - ``name``
     - Becomes the inventory host name.
   * - ``groups``, ``children``
     - Make the host a member of those inventory groups, as in step 4. Do not
       copy ``groups`` into ``mp``: both modules read it as the container's
       supplementary Unix groups.
   * - ``pre_build_image``
     - Delete it; the image is always used as given. When it was ``false`` or
       not set, molecule-plugins built an image with Python added. Use an image
       that already has Python, like the one above, or build one as for
       ``dockerfile``.
   * - ``dockerfile``, ``buildargs``
     - Build the image before running Molecule, or in a play placed before the
       import in ``create.yml``, and set ``image`` to the result.
   * - ``registry``
     - Log in to the registry before running Molecule (``podman login`` or
       ``docker login``), and use the full image reference in ``image``.
   * - podman ``security_opts``, ``devices``
     - Rename to ``security_opt`` and ``device``.
   * - podman ``tmpfs``
     - Change the list to a mapping of path to options:

       .. code-block:: yaml

          tmpfs:
            /tmp: rw
            /run: rw
   * - podman ``cgroup_manager``, ``extra_opts``
     - Pass the raw flags in ``cmd_args``, for example
       ``--cgroup-manager=cgroupfs`` as an item of the ``cmd_args`` list.
   * - docker ``override_command``
     - Delete it. Without a ``command``, the container runs ``sleep infinity``;
       set ``command: null`` to keep the image's own command.
   * - docker ``purge_networks``
     - Delete it; ``networks`` lists every network the container joins.

Other drivers
-------------

Scenarios that used a VM driver move the same way, but the instance settings
differ. Use the ``qemu`` backend for local virtual machines, ``kubevirt`` for
virtual machines on Kubernetes, and ``vmware`` for vSphere; each backend guide
lists its settings with an example.
