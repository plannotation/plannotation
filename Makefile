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
EXAMPLES_DIR   ?= examples
BENCH_MODEL    ?= claude-opus-5
BENCH_N        ?= 100

# cairosvg (LGPL-3.0-or-later, used unmodified) reaches the system libcairo
# through cffi, and ctypes.util.find_library does not search Homebrew's prefix on
# macOS -- `import cairosvg` then dies with `no library called "cairo-2" was
# found` even with cairo installed. Point the dynamic loader at it so `make samples`
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
        cov licenses samples samples-check examples examples-check bench bench-dry site \
        site-serve inspector hooks precommit build clean distclean version

help: ## Show this help
	@printf 'Plannotation targets:\n\n'
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
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

cov: ## Tests with separate coverage floors for the core and for infer/
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

# Real buildings, drawn from openly licensed IFC models (docs/examples.md). Each model
# is pinned by URL, size and SHA-256 and downloaded once into .cache/examples/, or the
# directory PLANNOTATION_EXAMPLES_CACHE names; a file whose hash differs is refused.
# Needs the svg and ifc extras. Rebuilding writes byte-identical files.
examples: ## Build the real example sheets (downloads the pinned models once)
	$(RUN) python tools/build_examples.py --out $(EXAMPLES_DIR)

# Builds the examples, then validates each sheet against the model it was drawn from:
# every element cross-checked, every dimension between two elements re-measured. Any
# finding, a warning included, fails it. CI runs it on Linux.
examples-check: ## Build the example sheets and validate each against its own model
	$(RUN) python tools/build_examples.py --out $(EXAMPLES_DIR) --check

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

# The public site, built by the same two commands the Pages workflow runs. Every
# schema is staged at the path of its own $id and the spec at SPEC_URI, both read from
# the code, so the published layout cannot drift from what documents declare; the
# landing page shows the example sheets in $(EXAMPLES_DIR)/, if they are built. The
# renderer's two packages are not project dependencies, so uv supplies them for that
# one command and uv.lock does not change.
SITE_DIR      ?= site
SITE_EXAMPLES  = $(if $(wildcard $(EXAMPLES_DIR)/index.json),--examples $(EXAMPLES_DIR))
RENDER        := PYTHONPATH=$(CURDIR) $(UV) run --no-project \
                 --with markdown-it-py==4.2.0 --with mdit-py-plugins==0.6.1 python

site: ## Build the public site into site/: stage it, then render its Markdown
	rm -rf $(SITE_DIR)
	$(RUN) python tools/stage_site.py $(SITE_DIR) $(SITE_EXAMPLES)
	$(RENDER) tools/render_site.py $(SITE_DIR) $(SITE_EXAMPLES)

site-serve: ## Serve site/ at http://localhost:8000
	python3 -m http.server -d $(SITE_DIR) 8000

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

version: ## Print the version the CLI reports
	$(RUN) plannotation --version

clean: ## Remove build and test artefacts
	rm -rf dist build site .coverage coverage.xml htmlcov .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

distclean: clean ## Also remove the virtualenv
	rm -rf .venv
