# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project type

Ansible Collection `david_igou.molecule_provisioners`, scaffolded by `ansible-creator`. Targets `ansible-core >= 2.15`. The collection FQCN appears in code paths and tests as `david_igou.molecule_provisioners`; the collection must live at `ansible_collections/david_igou/molecule_provisioners/` for tooling (pytest, ansible-lint, molecule) to resolve plugins. If working outside that structure, symlink or clone into a path matching it.

`AGENTS.md` defers all conventions to <https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md> — fetch and follow it when in doubt.

## Common commands

| Task | Command |
| --- | --- |
| Install runtime/test deps | `pip install -r requirements.txt -r test-requirements.txt` |
| Run unit tests | `pytest tests/unit` |
| Run a single unit test | `pytest tests/unit/test_basic.py::test_basic` |
| Run molecule-driven integration tests | `pytest tests/integration` |
| Run one molecule scenario directly | `cd extensions/molecule/<scenario> && molecule test` |
| Ansible sanity tests | `ansible-test sanity --docker` (run from collection root inside the `ansible_collections/...` tree) |
| Ansible-galaxy unit tests | `ansible-test units --docker` |
| Lint | `ansible-lint` and `pre-commit run --all-files` |
| tox-ansible matrix | `tox -c tox-ansible.ini` (skips py3.7/3.8 and ansible 2.9–2.13 per `tox-ansible.ini`) |
| Build collection artifact | `ansible-galaxy collection build` |

`pyproject.toml` configures pytest with `-n 2` (xdist parallel) and sets `testpaths = ["tests"]`. `pytest tests/integration` and `pytest tests/unit` both work from the repo root.

## Pre-commit

`.pre-commit-config.yaml` runs `update-docs` (ansible-network/collection_prep — regenerates README plugin sections), `prettier`, `isort` (black profile, line 100), `black` (line 100), `flake8`, plus `no-commit-to-branch` against `main`. Don't bypass with `--no-verify`. Direct commits to `main` are blocked — work on a branch.

## Architecture

### Plugin layout (`plugins/`)
Standard Ansible Collection plugin tree: `action/`, `cache/`, `filter/`, `inventory/`, `lookup/`, `module_utils/`, `modules/`, `plugin_utils/`, `sub_plugins/`, `test/`. Most directories are scaffold-only (`__init__.py` plus a `sample_*.py`). When adding a new plugin, follow the `sample_module.py` / `sample_filter.py` pattern — they include the `DOCUMENTATION`/`EXAMPLES`/`RETURN` blocks that `ansible-test sanity` and `update-docs` rely on.

### Roles (`roles/`)
Single role `run` with the standard subdirs (`tasks/`, `defaults/`, `meta/`, `vars/`, `handlers/`, `templates/`, `files/`, `tests/`). `meta/argument_specs.yml` documents role inputs — keep it in sync when adding variables, since validation runs from it.

### Tests and integration framework (`tests/` + `extensions/molecule/`)
This collection uses an unusual pytest-ansible + molecule integration pattern:

- `tests/integration/test_integration.py` defines a single parameterized test that takes a `molecule_scenario` fixture (from `pytest_ansible.molecule`) and runs `.test()` on it. Each scenario directory under `extensions/molecule/` becomes one parameterization automatically.
- A scenario directory named `integration_<name>` (e.g. `extensions/molecule/integration_hello_world/`) runs the integration target at `tests/integration/targets/<name>/`. The mapping is computed in `extensions/molecule/utils/playbooks/converge.yml`, which strips the `integration_` prefix and includes that role.
- `extensions/molecule/utils/vars/vars.yml` resolves paths via `MOLECULE_PROJECT_DIRECTORY` (set by molecule). `collection_root = $MOLECULE_PROJECT_DIRECTORY/..` because molecule is invoked from inside the scenario dir.
- All scenarios share `cleanup`/`destroy`/`prepare` via `noop.yml` and converge via the shared playbook — scenario `molecule.yml` files only need to override platforms or sequence.

To add a new integration test:
1. Create `tests/integration/targets/<name>/tasks/main.yml` with the assertions.
2. Create `extensions/molecule/integration_<name>/molecule.yml` (copy `integration_hello_world/molecule.yml` as a template).
3. `pytest tests/integration` will pick it up.

### Changelogs
`changelogs/config.yaml` configures `antsibull-changelog`. Add news fragments under `changelogs/fragments/` rather than editing `CHANGELOG.rst` directly — the file is regenerated.

### CI
`.github/workflows/tests.yml` runs the reusable workflows from `ansible/ansible-content-actions` (changelog, build-import, ansible-lint, sanity, unit-galaxy) plus `ansible-network/github_actions` `unit_source.yml`. The `unit-source` job pre-installs `ansible.utils` from git. Release publishes to Galaxy via `release_galaxy.yaml` on GitHub release events.
