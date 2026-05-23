SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

# Make the collection discoverable as david_igou.molecule_provisioners without
# requiring a symlink into $HOME. pytest-ansible's molecule_scenario fixture
# populates collections/ansible_collections/david_igou/molecule_provisioners on
# its first run; lint / build / sanity reuse it from here.
export ANSIBLE_COLLECTIONS_PATH := $(CURDIR)/collections:$(HOME)/.ansible/collections

CANONICAL := $(CURDIR)/.build/ansible_collections/david_igou/molecule_provisioners

SCENARIO_DIR := extensions/molecule/default

.PHONY: help install lint \
        test test-podman test-kubevirt test-docker test-qemu \
        podman kubevirt docker qemu \
        sanity build pre-commit clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install python + ansible collection dependencies
	python -m pip install --upgrade pip
	pip install ansible-core -r requirements.txt -r test-requirements.txt
	ansible-galaxy collection install containers.podman kubernetes.core community.crypto

lint: ## Run ansible-lint and yamllint
	ansible-lint
	yamllint .

test: test-podman ## Alias for test-podman (the kubevirt backend needs a live cluster)

test-podman: ## pytest-driven podman self-test (tests/integration -k default)
	PROVISIONER=podman pytest tests/integration -v -k default

test-kubevirt: ## pytest-driven kubevirt self-test (needs KUBECONFIG with KubeVirt)
	PROVISIONER=kubevirt pytest tests/integration -v -k default -s -o addopts=""

test-docker: ## pytest-driven docker self-test
	PROVISIONER=docker pytest tests/integration -v -k default

test-qemu: ## pytest-driven qemu self-test (needs qemu-system-x86_64 + virtqemud)
	PROVISIONER=qemu pytest tests/integration -v -k default

podman: ## `molecule test` against podman directly (bypasses pytest)
	cd $(SCENARIO_DIR) && PROVISIONER=podman molecule test

kubevirt: ## `molecule test` against kubevirt directly (needs KUBECONFIG with KubeVirt)
	cd $(SCENARIO_DIR) && PROVISIONER=kubevirt molecule test

docker: ## `molecule test` against docker directly
	cd $(SCENARIO_DIR) && PROVISIONER=docker molecule test

qemu: ## `molecule test` against qemu directly (needs qemu-system-x86_64 + virtqemud)
	cd $(SCENARIO_DIR) && PROVISIONER=qemu molecule test

sanity: $(CANONICAL) ## Run ansible-test sanity inside a Docker container
	cd $(CANONICAL) && ansible-test sanity --docker $(SANITY_ARGS)

build: ## Build the collection artifact
	ansible-galaxy collection build --force

pre-commit: ## Run pre-commit on all files
	pre-commit run --all-files

clean: ## Remove build artifacts and pytest-ansible's symlinked collection tree
	rm -rf .build/ collections/ *.tar.gz

# ansible-test needs cwd to *resolve* (via realpath) to a directory inside an
# ansible_collections/<ns>/<name>/ tree. A plain symlink doesn't work because
# ansible-test follows it back to the repo root. Materialize per-item symlinks
# instead, matching what pytest-ansible's molecule_scenario fixture builds.
$(CANONICAL):
	@mkdir -p $(CANONICAL)
	@for item in $$(ls -A $(CURDIR) | grep -v '^\.build$$'); do \
	  ln -sfn $(CURDIR)/$$item $(CANONICAL)/$$item; \
	done
