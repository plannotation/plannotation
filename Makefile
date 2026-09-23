# SPDX-License-Identifier: Apache-2.0
# Plannotation developer entry points. Written for GNU make 3.81 (the version Apple
# ships), so: no .ONESHELL, no $(file ...), no .RECIPEPREFIX, no undefine.

SHELL := /bin/sh
.DEFAULT_GOAL := help

UV             ?= uv
PYTHON_VERSION ?= 3.12
RUN            := $(UV) run --frozen
PKG            := plannotation
MCP_PKG        := plannotation_mcp
COV_MIN_CORE   ?= 85
COV_MIN_INFER  ?= 70
SAMPLES_DIR    ?= samples
BENCH_MODEL    ?= claude-opus-5
BENCH_N        ?= 100

# cairosvg (LGPL-3.0-or-later, used unmodified) reaches the system libcairo
# through cffi, and ctypes.util.find_library does not search Homebrew's prefix on
# macOS -- `import cairosvg` then dies with `no library called "cairo-2" was
# found` even with cairo installed. Point the dynamic loader at it so Phase 4
# works out of the box rather than looking like a packaging bug.
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
  ifneq ($(wildcard /opt/homebrew/lib/libcairo.2.dylib),)
    export DYLD_FALLBACK_LIBRARY_PATH := /opt/homebrew/lib:$(DYLD_FALLBACK_LIBRARY_PATH)
  else ifneq ($(wildcard /usr/local/lib/libcairo.2.dylib),)
    export DYLD_FALLBACK_LIBRARY_PATH := /usr/local/lib:$(DYLD_FALLBACK_LIBRARY_PATH)
  endif
endif


.PHONY: help install lock sync fmt fmt-check lint typecheck test fixtures check \
        cov licenses samples samples-check bench bench-dry docs inspector hooks precommit \
        build clean distclean version

help: ## Show this help
	@printf 'Plannotation targets:\n\n'
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@printf '\n'

install: ## Create the venv and install everything (dev group included)
	$(UV) sync --python $(PYTHON_VERSION) --all-extras

lock: ## Refresh uv.lock
	$(UV) lock

sync: ## Install exactly what uv.lock says
	$(UV) sync --frozen --all-extras

fmt: ## Format the tree in place
	$(RUN) ruff format .
	$(RUN) ruff check --fix-only .

fmt-check: ## Check formatting without writing
	$(RUN) ruff format --check .

lint: ## Lint
	$(RUN) ruff check .

typecheck: ## Type-check both distributions under --strict
	$(RUN) mypy --strict $(PKG)
	$(RUN) mypy --strict $(MCP_PKG)/$(MCP_PKG)

test: ## Run the test suite
	$(RUN) pytest -q

# Schema validity is the floor, not the bar. This also checks that a dimension's
# value matches the length it draws at its viewport's scale, that a level agrees
# with its own paperToPlane, that no transform mirrors a view, and that
# plane.origin == storey.elevation + cutHeight. The first fixture set passed every
# schema check and was still physically impossible.
fixtures: ## Check the fixture corpus for geometric and canonical coherence
	$(RUN) python tools/check_fixtures.py

check: fmt-check lint typecheck test fixtures ## The gate: format, lint, types, tests, fixtures
	@printf '\nmake check: green\n'

cov: ## Tests with the two coverage thresholds of the design brief section 15
	$(RUN) coverage erase
	$(RUN) pytest -q --cov=$(PKG) --cov=$(MCP_PKG) --cov-report=term-missing --cov-report=xml
	@printf '\ncore (>= $(COV_MIN_CORE)%%, plannotation excluding infer/):\n'
	$(RUN) coverage report --fail-under=$(COV_MIN_CORE) --omit='$(PKG)/infer/*'
	@printf '\ninference (>= $(COV_MIN_INFER)%%, plannotation/infer only):\n'
	$(RUN) coverage report --fail-under=$(COV_MIN_INFER) --include='$(PKG)/infer/*'

licenses: ## Audit dependency licences against the policy; rewrite THIRD_PARTY_LICENSES.md
	$(RUN) python tools/audit_licenses.py --write THIRD_PARTY_LICENSES.md

# Needs the svg and ifc extras, which `make install` provides. The output is
# reproducible: rebuilding writes byte-identical files.
samples: ## Build the three reference sample sets
	$(RUN) plannotation samples build --out $(SAMPLES_DIR)

samples-check: samples ## Build the samples and validate every one of them
	@for name in floorplan positionsplan section; do \
	  printf '%-16s' "$$name"; \
	  $(RUN) plannotation validate $(SAMPLES_DIR)/$$name/sheet.plannotated.pdf >/dev/null \
	    && echo "valid" || exit 1; \
	done

# The only target that calls a paid API. It needs ANTHROPIC_API_KEY in the
# environment or in .env (git-ignored), and only for questions whose response is not
# already in bench/cache/; a repeated run is free. CI never runs it.
bench: samples ## Run the plannotated-vs-plain benchmark and update the README table
	$(RUN) plannotation-bench questions
	$(RUN) plannotation-bench run --condition plain       --model $(BENCH_MODEL) --n $(BENCH_N)
	$(RUN) plannotation-bench run --condition plannotated --model $(BENCH_MODEL) --n $(BENCH_N)
	$(RUN) plannotation-bench report --model $(BENCH_MODEL)
	$(RUN) plannotation-bench readme --model $(BENCH_MODEL)

bench-dry: samples ## Say how many benchmark questions would reach the API; ask none
	$(RUN) plannotation-bench run --condition plain       --model $(BENCH_MODEL) --n $(BENCH_N) --dry-run
	$(RUN) plannotation-bench run --condition plannotated --model $(BENCH_MODEL) --n $(BENCH_N) --dry-run

# Every schema is staged at the path of its own $id and the spec at SPEC_URI, both
# read from the code, so the published layout cannot drift from what documents
# declare.
docs: ## Stage the schemas, the spec and the README into site/ for GitHub Pages
	$(RUN) python tools/stage_site.py site

# The inspector is one static file with no build step; this only makes sure there is
# a plannotated drawing to open in it. `open` is macOS, `xdg-open` elsewhere.
inspector: samples ## Build the samples and open the single-file inspector
	@printf 'open %s/floorplan/sheet.plannotated.pdf in the page that opens\n' '$(SAMPLES_DIR)'
	@if command -v open >/dev/null 2>&1; then open inspector/index.html; \
	 elif command -v xdg-open >/dev/null 2>&1; then xdg-open inspector/index.html; \
	 else printf 'open inspector/index.html in a browser\n'; fi

hooks: ## Install the git hooks
	# default_install_hook_types in .pre-commit-config.yaml already covers
	# commit-msg, so a second `install --hook-type` call would only leave a
	# spurious .git/hooks/commit-msg.legacy behind.
	$(RUN) pre-commit install --install-hooks

precommit: ## Run every pre-commit hook over the whole tree
	$(RUN) pre-commit run --all-files --show-diff-on-failure

build: ## Build wheels and sdists for both distributions
	$(UV) build --all-packages --out-dir dist

version: ## Print the version the CLI reports (the Phase 0 gate)
	$(RUN) plannotation --version

clean: ## Remove build and test artefacts
	rm -rf dist build site .coverage coverage.xml htmlcov .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

distclean: clean ## Also remove the virtualenv
	rm -rf .venv
