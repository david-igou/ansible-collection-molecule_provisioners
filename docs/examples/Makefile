# Copy to your collection root. Running from the collection root (not from
# extensions/) is what lets molecule auto-discover extensions/molecule/config.yml
# and therefore engage the pinned dependency step.
export MOLECULE_GLOB := extensions/molecule/*/molecule.yml

.PHONY: test
test:
	molecule test --all

.PHONY: converge
converge:
	molecule converge --all

.PHONY: destroy
destroy:
	molecule destroy --all
