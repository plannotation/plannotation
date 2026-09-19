# SPDX-License-Identifier: Apache-2.0
# PlanLabel developer entry points. Written for GNU make 3.81 (the version Apple
# ships), so: no .ONESHELL, no $(file ...), no .RECIPEPREFIX, no undefine.

SHELL := /bin/sh
.DEFAULT_GOAL := help

UV             ?= uv
PYTHON_VERSION ?= 3.12
RUN            := $(UV) run --frozen
PKG            := planlabel
MCP_PKG        := planlabel_mcp
COV_MIN_CORE   ?= 85
COV_MIN_INFER  ?= 70
SAMPLES_DIR    ?= samples
BENCH_MODEL    ?= claude-sonnet-4-5
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

# $(call not_yet,<target>,<phase>,<design-brief section>)
# One expanded line, so make 3.81 handles it without .ONESHELL.
not_yet = @printf '\nmake %s is not implemented yet: it arrives in Phase %s.\nSee the design brief, section %s.\n\n' '$(1)' '$(2)' '$(3)' >&2; exit 2

.PHONY: help install lock sync fmt fmt-check lint typecheck test check cov \
        licenses samples bench docs inspector hooks precommit build clean \
        distclean version

help: ## Show this help
	@printf 'PlanLabel targets:\n\n'
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

check: fmt-check lint typecheck test ## The gate: format, lint, types, tests
	@printf '\nmake check: green\n'

cov: ## Tests with the two coverage thresholds of the design brief section 15
	$(RUN) coverage erase
	$(RUN) pytest -q --cov=$(PKG) --cov=$(MCP_PKG) --cov-report=term-missing --cov-report=xml
	@printf '\ncore (>= $(COV_MIN_CORE)%%, planlabel excluding infer/):\n'
	$(RUN) coverage report --fail-under=$(COV_MIN_CORE) --omit='$(PKG)/infer/*'
	@printf '\ninference (>= $(COV_MIN_INFER)%%, planlabel/infer only):\n'
	$(RUN) coverage report --fail-under=$(COV_MIN_INFER) --include='$(PKG)/infer/*'

licenses: ## Audit dependency licences against the policy; rewrite THIRD_PARTY_LICENSES.md
	$(RUN) python tools/audit_licenses.py --write THIRD_PARTY_LICENSES.md

samples: ## Build the three reference sample sets (Phase 4)
	@if [ -f $(PKG)/export/ifc_svg_pdf.py ] && grep -q 'def build' $(PKG)/export/ifc_svg_pdf.py 2>/dev/null; then \
	  $(RUN) planlabel samples build --out $(SAMPLES_DIR); \
	else \
	  printf '\nmake samples is not implemented yet: it arrives in Phase 4.\nSee the design brief, section 9.\nIt needs the svg and ifc extras: make install\n\n' >&2; exit 2; \
	fi

bench: ## Run the labelled-vs-plain benchmark (Phase 8)
	@if [ -f bench/runner.py ]; then \
	  $(RUN) planlabel-bench run --condition plain    --model $(BENCH_MODEL) --n $(BENCH_N); \
	  $(RUN) planlabel-bench run --condition labelled --model $(BENCH_MODEL) --n $(BENCH_N); \
	  $(RUN) planlabel-bench report; \
	else \
	  printf '\nmake bench is not implemented yet: it arrives in Phase 8.\nSee the design brief, section 13.\nIt needs ANTHROPIC_API_KEY in .env; nothing in CI ever calls it.\n\n' >&2; exit 2; \
	fi

# The schema version is read from planlabel.SCHEMA_VERSION rather than repeated
# here, so the published URL layout cannot drift from the one constant that
# defines it.
docs: ## Stage spec/, schema/ and README into site/ for GitHub Pages
	@V=`$(RUN) python -c 'import planlabel; print(planlabel.SCHEMA_VERSION)'`; \
	 mkdir -p "site/schema/$$V" site/spec; \
	 cp -f $(PKG)/schema/*.json "site/schema/$$V/" 2>/dev/null || true; \
	 cp -f spec/SPEC.md site/spec/; \
	 cp -f README.md site/index.md; \
	 printf 'docs staged in site/ (schema %s) -- publish with GitHub Pages\n' "$$V"

inspector: ## Open the single-file inspector (Phase 5)
	$(call not_yet,inspector,5,10)

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
	$(RUN) planlabel --version

clean: ## Remove build and test artefacts
	rm -rf dist build site .coverage coverage.xml htmlcov .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

distclean: clean ## Also remove the virtualenv
	rm -rf .venv
