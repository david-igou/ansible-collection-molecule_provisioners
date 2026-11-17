.. _ansible_collections.david_igou.molecule_provisioners.docsite.guide_migration:

Migration
=========

From a ``platforms:`` scenario
------------------------------

Molecule's older scenario shape lists instances under ``platforms:`` and picks a
``driver:``. With this collection the instances move into inventory and the
lifecycle playbooks become imports.

1. Pin the collection in ``extensions/molecule/requirements-test.yml`` and share
   the Molecule settings through ``extensions/molecule/config.yml``. Copy both
   from `docs/examples/ <https://github.com/david-igou/ansible-collection-molecule_provisioners/tree/main/docs/examples>`__.
2. Reduce each scenario's ``molecule.yml`` to its name. Delete ``driver:``,
   ``platforms:`` and ``provisioner:``, and drop ``molecule-plugins`` from your
   requirements.
3. Replace ``create.yml``, ``destroy.yml`` and ``prepare.yml`` with imports of
   ``david_igou.molecule_provisioners.create``, ``.destroy`` and ``.prepare``.
   Plays that did scenario-specific setup can stay before or after the import.
4. Move every platform into ``inventory/hosts.yml`` as a host of the ``molecule``
   group, with its settings under ``mp.<backend>``. Put shared settings in
   ``mp_defaults.<backend>`` and ``mp_backend`` in
   ``inventory/group_vars/molecule.yml``.

For Podman and Docker platforms, keep the key names Molecule passed to the
container modules (``image``, ``command``, ``privileged``, ``volumes``, ``env``, ...);
the table below covers the few that differ.

From 0.0.7-alpha
----------------

The playbook imports and the ``mp_backend``/``mp_defaults``/``mp`` layout are
unchanged. A host no longer needs an ``mp.<backend>`` block when
``mp_defaults.<backend>`` covers it. Settings changed as follows.

All backends
~~~~~~~~~~~~

- The ``aws`` backend and the ``reset`` playbook are removed. The ``vmware`` backend
  on ``main`` after 0.0.7-alpha was never released; its replacement is described
  in the :ref:`VMware backend guide <ansible_collections.david_igou.molecule_provisioners.docsite.backend_vmware>`.
- Role defaults dicts are renamed from ``mp_<backend>_role_defaults`` to
  ``mp_<backend>_instance_defaults``.
- Molecule runs the lifecycle against the molecule hosts, so role variables such
  as ``mp_kubevirt_wait_timeout`` can live in ``group_vars/molecule.yml``, in
  ``group_vars/all.yml``, or on single hosts.

podman and docker
~~~~~~~~~~~~~~~~~

``mp.podman`` and ``mp.docker`` are now passed unchanged to
``containers.podman.podman_container`` and ``community.docker.docker_container``.
Any option of those modules works, and keys must use the module's names. The
default command is ``sleep infinity``, and prepare no longer installs ``sudo``.

+------------------------------------------------+------------------------------------------------+
| 0.0.7-alpha                                    | Now                                            |
+================================================+================================================+
| podman ``podman_network``                      | ``network`` (names) plus                       |
|                                                | ``mp_podman_networks`` (``podman_network``     |
|                                                | arguments)                                     |
+------------------------------------------------+------------------------------------------------+
| podman ``security_opts``                       | ``security_opt``                               |
+------------------------------------------------+------------------------------------------------+
| podman ``devices``                             | ``device``                                     |
+------------------------------------------------+------------------------------------------------+
| podman ``restart_retries: 3``                  | ``restart_policy: on-failure:3``               |
+------------------------------------------------+------------------------------------------------+
| podman ``extra_opts``, ``cgroup_manager``,     | ``cmd_args`` (raw ``podman`` flags)            |
| ``storage_opt``, ``storage_driver``            |                                                |
+------------------------------------------------+------------------------------------------------+
| docker ``networks`` creation                   | ``mp_docker_networks`` (``docker_network``     |
|                                                | arguments); ``networks`` only attaches         |
+------------------------------------------------+------------------------------------------------+
| docker ``override_command: false``             | ``command: null``                              |
+------------------------------------------------+------------------------------------------------+
| docker ``purge_networks``                      | removed                                        |
+------------------------------------------------+------------------------------------------------+

qemu
~~~~

+------------------------------------------------+------------------------------------------------+
| 0.0.7-alpha                                    | Now                                            |
+================================================+================================================+
| ``application_ports``, ``networks``, ``nics``  | ``extra_args``                                 |
|                                                | (``-netdev user,...,hostfwd=...`` and          |
|                                                | ``-device``)                                   |
+------------------------------------------------+------------------------------------------------+
| ``connection: network_cli``,                   | Override ``prepare.yml`` with your own         |
| ``connection_probe``                           | readiness plays                                |
+------------------------------------------------+------------------------------------------------+
| ``diagnostics``                                | Prepare prints the end of the serial log on    |
|                                                | failure                                        |
+------------------------------------------------+------------------------------------------------+
| ``mp_qemu_run_isolation``,                     | ``mp_qemu_ssh_port_base`` (each run takes 20   |
| ``mp_qemu_slirp_port_base``, ``ssh_host_port`` | ports from it)                                 |
+------------------------------------------------+------------------------------------------------+
| ``mp_qemu_image_cache_dir``,                   | ``mp_qemu_cache_dir`` (images only; run files  |
| ``mp_qemu_runtime_cache_dir``                  | live in the ephemeral directory)               |
+------------------------------------------------+------------------------------------------------+

Destroy guests created by 0.0.7-alpha with 0.0.7-alpha before upgrading.

kubevirt
~~~~~~~~

+------------------------------------------------+------------------------------------------------+
| 0.0.7-alpha                                    | Now                                            |
+================================================+================================================+
| ``boot_source.type: data_volume_url`` with     | ``type: data_volume`` with                     |
| ``url``                                        | ``source: {http: {url: ...}}``                 |
+------------------------------------------------+------------------------------------------------+
| ``boot_source.type: data_volume_pvc`` with     | ``type: data_volume`` with                     |
| ``source``                                     | ``source: {pvc: {name: ..., namespace: ...}}`` |
+------------------------------------------------+------------------------------------------------+
| ``boot_source.type: data_volume_source_ref``   | ``type: data_volume`` with ``source_ref``      |
+------------------------------------------------+------------------------------------------------+
| ``extra_disks``, ``extra_volumes``,            | ``vm_overrides`` (lists are appended)          |
| ``extra_interfaces``, ``extra_networks``       |                                                |
+------------------------------------------------+------------------------------------------------+
| ``vm_definition``                              | ``vm_overrides``, or                           |
|                                                | ``interfaces``/``networks``/``data_disks``     |
+------------------------------------------------+------------------------------------------------+
| ``admin_user``, ``admin_password``             | ``connection_vars.ansible_user``,              |
|                                                | ``connection_vars.ansible_password``           |
+------------------------------------------------+------------------------------------------------+
| ``cloud_init.user_data_secret``,               | ``cloud_init.enabled: false`` plus a           |
| ``network_data_secret``, ``inject_ssh_key``    | cloud-init volume in ``vm_overrides``          |
+------------------------------------------------+------------------------------------------------+
| ``instancetype``/``preference`` as a mapping   | The name as a string                           |
+------------------------------------------------+------------------------------------------------+
| ``memory_limit``                               | ``vm_overrides``                               |
+------------------------------------------------+------------------------------------------------+
| ``mp_kubevirt_run_isolation``,                 | Removed; every run is isolated and             |
| ``mp_kubevirt_cleanup_timeout``                | ``mp_kubevirt_wait_timeout`` covers cleanup    |
+------------------------------------------------+------------------------------------------------+

Resource names are now ``<host>-<run id>`` and carry the label
``molecule-provisioners/run-id``. Destroy guests created by 0.0.7-alpha with
0.0.7-alpha before upgrading.
