.. _ansible_collections.david_igou.molecule_provisioners.docsite.backend_qemu:

QEMU backend
============

Runs each Molecule host as a local ``qemu-system-x86_64`` guest. Guests boot
from a copy-on-write overlay of a cached image, get a NoCloud cloud-init seed
with a generated management user and SSH key, and are reached over a
user-mode network SSH forward on ``127.0.0.1``.

Every option of the ``create``, ``destroy`` and ``prepare`` entry points and the per-host ``instance`` settings is listed in the :ref:`role reference <ansible_collections.david_igou.molecule_provisioners.qemu_role>`.

Scenarios use the collection's lifecycle playbooks; the role is not called
directly.

Requirements
------------

On the machine that runs Molecule:

- ``qemu-system-x86_64`` and ``qemu-img``
- the ``pycdlib`` Python package (seed ISO)
- OVMF for ``firmware: uefi`` (``edk2-ovmf`` on Fedora, ``ovmf`` on Debian/Ubuntu)
- ``/dev/kvm`` access for usable speed; TCG works but is slow

The collections they use are installed by Molecule's ``dependency`` step together with this collection.

Quick start
-----------

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
   mp_backend: qemu
   mp_defaults:
     qemu:
       image: https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
       ssh_user: debian
       memory: 2048

.. code:: yaml

   # extensions/molecule/default/inventory/hosts.yml
   ---
   all:
     children:
       molecule:
         hosts:
           instance:
             mp:
               qemu:
                 disk_size: 20G

Host fields
-----------

Set under ``mp.qemu`` per host or ``mp_defaults.qemu`` for all hosts; the host
wins. Unknown fields fail validation.
Every setting, with its type and default, is listed under the :ref:`instance settings <ansible_collections.david_igou.molecule_provisioners.qemu_role__entrypoint-instance>`.

How it works
------------

- **create**: validates the merged settings, downloads the image into the
  cache (once per image), unpacks it, then writes everything for the run to
  ``$MOLECULE_EPHEMERAL_DIRECTORY/qemu/``: an overlay qcow2, data disks, the
  seed ISO, serial log (``<host>.log``) and pidfile. The guest runs daemonized
  as ``-name <host>-<run id>``. KVM is used when ``/dev/kvm`` is accessible.
- **prepare**: waits for an Ansible connection; on timeout the error shows the
  end of the serial log.
- **destroy**: stops each guest whose process command line still carries the
  run's name (a stale pidfile whose PID was reused is ignored), then removes the
  run directory. It succeeds when nothing was created.

Notes
-----

- **Appliance guests** without cloud-init or Python (for example MikroTik
  CHR) boot fine; the seed ISO is ignored. Ship your own ``prepare.yml`` that
  waits on the port and bootstraps the device, and override connection
  settings in your inventory.

- **Extra NICs and port forwards** go in ``extra_args``:

  .. code:: yaml

     # in mp_defaults.qemu, or under one host's mp.qemu
     extra_args:
       - -netdev
       - user,id=api,net=10.0.3.0/24,hostfwd=tcp::8728-10.0.3.15:8728
       - -device
       - virtio-net-pci,netdev=api

  Fixed host ports in ``hostfwd`` are shared by every run on the controller.
