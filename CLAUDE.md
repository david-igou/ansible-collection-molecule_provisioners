# CLAUDE.md

Ansible collection `david_igou.molecule_provisioners`: Molecule `create`,
`prepare` and `destroy` playbooks for the `podman`, `docker`, `qemu`,
`kubevirt` and `vmware` backends. Targets ansible-core 2.19+. The project is alpha; breaking
changes do not need a version bump.

Follow [AGENTS.md](AGENTS.md). Keep plans and session notes out of the repository.

## Layout

- `playbooks/{create,destroy,prepare}.yml` — what consumers import. Create and
  destroy target `hosts: molecule` with `playbooks/vars/controller.yml`, which
  forces a local connection, lists the backends and defines `mp_run_id`.
  Create includes `roles/<mp_backend>` (`tasks_from: create`) and writes every
  host's `mp_runtime` fact to `$MOLECULE_EPHEMERAL_DIRECTORY/inventory/molecule_runtime.yml`.
- `roles/<backend>/vars/main.yml` — `_mp_spec`, the merge of
  `mp_<backend>_instance_defaults`, `mp_defaults.<backend>` and `mp.<backend>`.
- `roles/{qemu,kubevirt,vmware}/meta/argument_specs.yml` — the `instance` entry point
  is the per-host schema (`ansible-doc -t role ... -e instance`); create
  validates `_mp_spec` against it.
- `roles/{podman,docker}` — `_mp_spec` is passed to the container module through
  `module_defaults`, so the schema is the module's own.
- `extensions/molecule/` — self-test scenarios (`containers`, `qemu`,
  `kubevirt`, `vmware`) sharing `config.yml`.
- `docs/docsite/` — the documentation site (`make docs`): guides in `rst/`,
  their order in `extra-docs.yml`. Role pages come from `meta/argument_specs.yml`.
  `.github/workflows/docs.yml` publishes it to GitHub Pages from `main`.
- `docs/examples/` — files consumers copy.

## Commands

| Task | Command |
| --- | --- |
| Install dependencies | `make install` |
| Lint | `make lint` |
| Container scenarios | `make test` (`make test-podman`, `make test-docker`) |
| QEMU scenario | `make test-qemu` |
| KubeVirt scenario | `make test-kubevirt` (uses `KUBECONFIG`) or `scripts/test-kubevirt-kind.sh` |
| VMware scenario | `make test-vmware` (uses `VMWARE_*` and `MP_TEST_VMWARE_*`) |
| Sanity | `make sanity` |
| Build | `make build` |
| Docs site | `make docs` (needs `docs-requirements.txt`) |

Run scenarios from the collection root; the Makefile sets `MOLECULE_GLOB` and
puts `.build/` (this checkout) first on the collection path. `make test-kubevirt`
and `make test-vmware` create VMs on whatever cluster or vCenter the environment
selects, so live-infrastructure authorization rules apply.

## Changing a backend

1. Edit `roles/<backend>/`. For qemu, kubevirt and vmware, a new host setting needs an
   entry in `mp_<backend>_instance_defaults` and the `instance` argument spec.
2. Cover the behavior in the backend's scenario `verify.yml`.
3. Update the backend guide in `docs/docsite/rst/backend_<backend>.rst` and add a
   changelog fragment.
4. Run `make lint`, `make docs` and the backend's scenario.

## Constraints

- No Python plugins and no `molecule-plugins` dependency.
- `var-naming[no-role-prefix]` is skipped: user variables share the `mp_` prefix.
- ansible-lint runs the production profile; every play, including
  `import_playbook`, needs a `name`.
