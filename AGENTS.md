<!--# cspell: ignore SSOT CMDB -->

# AGENTS.md

Ensure that all practices and instructions described by
<https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md>
are followed.

## Agent working files

Never commit agent plans, design notes, session logs, or writing-tool state.
Keep them outside this repository. Put user-facing documentation in the
documentation site sources under `docs/docsite/`, or in `README.md`.

## Rules for this collection

- Ansible only: no Python plugins, modules or filters. Reach for ansible-core
  and the declared dependency collections first.
- Validate VM settings with the role's `instance` argument spec and
  `ansible.builtin.validate_argument_spec`; add an assert only for a rule that
  spans several fields.
- Use `no_log` only on tasks that handle secrets.
- Tests are Molecule scenarios in `extensions/molecule/` that check behavior in
  the instances. Do not add tests that restate what a module or argument spec
  already guarantees.

## Verification

GitHub Actions runs lint only. Run `make lint` and the scenario for every
backend you changed (`make test`, `make test-qemu`, `make test-kubevirt`,
`make test-vmware`) from
the collection root. Do not add hosted test matrices or backend jobs to Actions.

## Adding a Molecule scenario to a consumer collection

- [ ] Pin `david_igou.molecule_provisioners` to an exact version in
      `extensions/molecule/requirements-test.yml`.
- [ ] Share it through `extensions/molecule/config.yml` so every scenario uses
      one pin.
- [ ] Run Molecule from the collection root with `MOLECULE_GLOB` set.
- [ ] Commit an `ansible.cfg` at the collection root.

Copy all four from [`docs/examples/`](docs/examples/).
