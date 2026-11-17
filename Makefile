SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

# Scenarios run from the collection root so Molecule merges
# extensions/molecule/config.yml. They test this checkout through .build/,
# which comes first on the collection path.
COLLECTION := $(CURDIR)/.build/ansible_collections/david_igou/molecule_provisioners
export ANSIBLE_COLLECTIONS_PATH := $(CURDIR)/.build:$(ANSIBLE_COLLECTIONS_PATH)
export MOLECULE_GLOB := extensions/molecule/*/molecule.yml

.PHONY: help install lint test test-podman test-docker test-qemu test-kubevirt test-vmware sanity build clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_%-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install Python requirements and dependency collections
	python -m pip install -r lint-requirements.txt -r requirements.txt -r test-requirements.txt
	ansible-galaxy collection install . --force

lint: ## Lint Ansible content, YAML and changelog fragments
	ansible-lint
	yamllint .
	antsibull-changelog lint

test: test-podman test-docker ## Run the container backend scenarios

test-podman test-docker: test-%: $(COLLECTION) ## Run the containers scenario on one container backend
	PROVISIONER=$* molecule test -s containers

test-qemu test-kubevirt test-vmware: test-%: $(COLLECTION) ## Run one VM backend scenario
	molecule test -s $*

sanity: $(COLLECTION) ## Run ansible-test sanity in a container
	cd $(COLLECTION) && ansible-test sanity --docker $(SANITY_ARGS)

build: ## Build the collection artifact
	ansible-galaxy collection build --force

clean: ## Remove build artifacts
	rm -rf .build *.tar.gz

# ansible-test resolves the real path of its working directory, so a single
# symlink to the checkout is not enough; link each top-level item instead.
$(COLLECTION):
	@mkdir -p $(COLLECTION)
	@for item in $$(ls -A $(CURDIR) | grep -v '^\.build$$'); do ln -sfn $(CURDIR)/$$item $(COLLECTION)/$$item; done
