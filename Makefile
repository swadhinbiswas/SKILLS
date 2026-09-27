# Convenience targets. Everything here is a thin wrapper around the same
# commands CI runs, so `make check` locally == green CI.
#
#   make check     validate everything (spec, index, installer, all tests)
#   make validate  spec compliance only
#   make index     regenerate catalog.json + INDEX.md
#   make test      every test suite
#   make install   install into detected agents
#   make clean     remove caches

PYTHON ?= python3

.PHONY: check validate index installer test skill-tests lint clean install help

help:
	@grep -E '^#   make' $(MAKEFILE_LIST) | sed 's/^#   //'

check: validate index-lint installer test
	@echo
	@echo "all checks passed"

validate:
	@$(PYTHON) tools/validate_skills.py

index:
	@$(PYTHON) tools/build_index.py

index-lint:
	@$(PYTHON) tools/build_index.py --check

installer:
	@bash -n install.sh
	@./install.sh --help > /dev/null
	@./install.sh --list > /dev/null
	@echo "installer OK"

test: skill-tests
	@echo
	@$(PYTHON) -m unittest discover -s tests

skill-tests:
	@failed=0; \
	for skill in $$(find skills -path '*/scripts/*.py' -printf '%h\n' | xargs -n1 dirname | sort -u); do \
		echo "--- $$skill"; \
		( cd "$$skill" && $(PYTHON) -m unittest discover -s tests ) || failed=1; \
	done; \
	exit $$failed

install:
	@./install.sh

clean:
	@find . -name __pycache__ -type d -not -path './.git/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete 2>/dev/null || true
	@echo "cleaned"
