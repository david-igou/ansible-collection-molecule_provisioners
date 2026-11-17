.. _ansible_collections.david_igou.molecule_provisioners.docsite.backend_vmware:

VMware vSphere backend
======================

Runs each Molecule host as a VM cloned from a vCenter template. By default
the clone gets cloud-init metadata and user data through VMware GuestInfo
before its first boot: a management user with the run's SSH key and
passwordless sudo, and the inventory name as hostname. The controller
connects to the address VMware Tools reports.

Every option of the ``create``, ``destroy`` and ``prepare`` entry points and the per-host ``instance`` settings is listed in the :ref:`role reference <ansible_collections.david_igou.molecule_provisioners.vmware_role>`.

Scenarios use the collection's lifecycle playbooks; the role is not called
directly.

Quick start
-----------

.. code:: yaml

   # inventory/group_vars/molecule.yml
   mp_backend: vmware
   mp_defaults:
     vmware:
       datacenter: dc1
       cluster: compute
       folder: /dc1/vm/molecule
       datastore: ssd
       networks:
         - name: Lab DHCP
       ssh_user: tester
       hardware:
         num_cpus: 2
         memory_mb: 2048

.. code:: yaml

   # inventory/hosts.yml
   all:
     children:
       molecule:
         hosts:
           ubuntu:
             mp:
               vmware:
                 template: ubuntu-24.04-cloud
           centos:
             mp:
               vmware:
                 template: centos-stream9-cloud
                 hardware:
                   memory_mb: 4096

Host fields
-----------

Set under ``mp.vmware`` per host or ``mp_defaults.vmware`` for all hosts. The
role defaults, ``mp_defaults.vmware`` and ``mp.vmware`` are merged recursively in
that order; lists replace. Unknown fields fail validation.
Every setting, with its type and default, is listed under the :ref:`instance settings <ansible_collections.david_igou.molecule_provisioners.vmware_role__entrypoint-instance>`.

vCenter connection
------------------

The VMware modules read ``VMWARE_HOST``, ``VMWARE_USER``, ``VMWARE_PASSWORD``,
and optionally ``VMWARE_PORT`` and ``VMWARE_VALIDATE_CERTS``, from the controller
environment; AAP's VMware vCenter credential sets the same variables. Set
``mp_vmware_connection`` instead to pass them from inventory, keeping the
password in a vault or lookup in your inventory, not in the role:

.. code:: yaml

   mp_vmware_connection:
     hostname: vcenter.example.com
     username: molecule@vsphere.local
     password: "{{ vault_vcenter_password }}"

Certificates are validated by default. Add the vCenter CA to the controller's
trust store, or point ``SSL_CERT_FILE`` at it; use ``VMWARE_VALIDATE_CERTS=false``
only for a disposable lab.

The account needs, on the folder, datastore, network and resource pool: clone
from the template, create and delete VMs, change configuration (annotation,
advanced settings, CPU, memory, disks, NICs) and power operations.

Bootstrap
---------

- **``guestinfo``**: the template needs cloud-init with the VMware datasource
  (cloud-init 21.3 or later with open-vm-tools), and is reset so cloud-init runs
  again for a new instance id. The role sets ``guestinfo.metadata`` (instance id
  ``<host>-<run id>``, hostname) and ``guestinfo.userdata`` before power-on.
  Addressing is DHCP unless the template or ``cloud_init.user_data`` says
  otherwise. Prepare waits for ``cloud-init status --wait``.
- **``none``**: clone and power on only. The template must already accept a
  connection; supply credentials through ``connection_vars``
  (``ansible_password``, ``ansible_ssh_private_key_file``, ...).

vSphere guest customization is never used, so Windows/Sysprep guests are not
supported. NICs are configured with ``vmware_guest_network``, not
``vmware_guest``'s ``networks``: ``vmware_guest`` treats every ``networks`` entry as
DHCP and starts guest customization, which renames the guest to the VM name
and reboots it.

How it works
------------

- **create**: validates the merged settings, generates
  ``$MOLECULE_EPHEMERAL_DIRECTORY/identity_file``, clones the template powered
  off as ``<inventory_hostname>-<mp_run_id>`` with an annotation naming the run,
  sets the NICs' port groups and makes every NIC connect at power-on (clones
  can come out disconnected), starts the VM and waits for its IPv4 address. The runtime inventory gets ``ansible_host``, ``ansible_user``,
  the key, ``mp_vmware_name`` and ``mp_vmware_instance_uuid``.
- **prepare**: waits for an Ansible connection, then for cloud-init.
- **destroy**: looks up ``<inventory_hostname>-<mp_run_id>`` in ``folder``,
  refuses to delete it unless its annotation is this run's, then powers it
  off and deletes it with its disks. It succeeds when the VM does not exist.

Requirements
------------

- Collections ``community.vmware`` (6.x) and ``community.crypto``.
- Python ``pyvmomi`` on the controller.
- A route from the controller to the guest addresses VMware Tools reports.
  When guests are only reachable another way, set ``connection_vars``, e.g.
  ``ansible_ssh_common_args`` with a ``ProxyJump``.

Notes
-----

- Hosts run in parallel up to Ansible's ``forks``.
- A clone interrupted before vCenter sets its annotation is left alone by
  destroy; delete it by hand.
- The self-test scenario ``extensions/molecule/vmware`` reads its placement from
  ``MP_TEST_VMWARE_DATACENTER``, ``MP_TEST_VMWARE_CLUSTER``,
  ``MP_TEST_VMWARE_FOLDER``, ``MP_TEST_VMWARE_DATASTORE``,
  ``MP_TEST_VMWARE_NETWORK``, ``MP_TEST_VMWARE_TEMPLATE`` and
  ``MP_TEST_VMWARE_SECOND_TEMPLATE``; run it with ``make test-vmware``.
