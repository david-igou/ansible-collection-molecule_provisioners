<!--# cspell: ignore SSOT CMDB -->

# AGENTS.md

Ensure that all practices and instructions described by
<https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md>
are followed.

## Adding a Molecule scenario to a consumer collection

The checklist below is for agents adding a Molecule scenario to a **consumer**
collection that uses `david_igou.molecule_provisioners`. Following it yields a
**deterministic**, environment-independent test setup in one pass, matching the
maintainer's reference consumer
([`ansible-collection-armbian`](https://github.com/david-igou/ansible-collection-armbian)).

### Determinism checklist

- [ ] **Pin the provisioner version.** In `extensions/molecule/requirements-test.yml`,
      pin `david_igou.molecule_provisioners` to an exact Galaxy version (e.g.
      `0.0.1-alpha`) — never `version: main`. Copy `docs/examples/requirements-test.yml`.
- [ ] **Centralize the pin via `config.yml`.** Add `extensions/molecule/config.yml`
      pointing `dependency.options.requirements-file` at the pinned file, so every
      scenario shares one version and one bump point. Copy `docs/examples/config.yml`.
- [ ] **Run from the collection root with `MOLECULE_GLOB`.** `config.yml`
      auto-discovery fires only from the collection root. Use `docs/examples/Makefile`
      (`export MOLECULE_GLOB := extensions/molecule/*/molecule.yml`) and run `make test`.
- [ ] **Commit an `ansible.cfg`.** Set `collections_path` and bump the
      `[persistent_connection]` timeouts for `network_cli`-managed VM guests. Copy
      `docs/examples/ansible.cfg`.
- [ ] **Install backend prereqs explicitly.** See the README
      "Controller-host prerequisites by backend" section for the per-backend
      copy-paste install snippet, the `ansible-pylibssh` requirement for `network_cli`
      guests, and the OVMF path notes for `qemu` UEFI.

Do all five and re-running the same scenario a week later produces the same
provisioner behavior. See the [README](README.md) for the full consumer walkthrough.
