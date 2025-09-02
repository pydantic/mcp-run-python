.DEFAULT_GOAL := all

.PHONY: .uv
.uv: ## Check that uv is installed
	@uv --version || (echo '✖ Please install uv: https://docs.astral.sh/uv/getting-started/installation/' && exit 1)

.PHONY: .deno
.deno: ## Check that deno is installed
	@deno --version || (echo "✖ Please install deno: https://deno.com" && exit 1)

.PHONY: .pre-commit
.pre-commit: ## Check that pre-commit is installed
	@pre-commit -V || (echo '✖ Please install pre-commit: https://pre-commit.com/' && exit 1)

.PHONY: install
install: .uv .deno .pre-commit ## Install the package, dependencies, and pre-commit for local development
	uv sync --frozen
	pre-commit install --install-hooks

.PHONY: build
build: ## Build mcp_run_python/deno/prepareEnvCode.ts
	uv run build/build.py

.PHONY: format-ts
format-ts: ## Format TS code
	cd mcp_run_python/deno && deno task format

.PHONY: format-py
format-py: ## Format Python code
	uv run ruff format
	uv run ruff check --fix --fix-only

.PHONY: format
format: format-ts format-py ## Format all code

.PHONY: lint-ts
lint-ts: ## Lint TS code
	cd mcp_run_python/deno && deno task lint

.PHONY: lint-py
lint-py: ## Lint Python code
	uv run ruff format --check
	uv run ruff check

.PHONY: lint
lint:  lint-ts lint-py ## Lint all code

.PHONY: typecheck-ts
typecheck-ts: build ## Typecheck TS code
	cd mcp_run_python/deno && deno task typecheck

.PHONY: typecheck-py
typecheck-py: ## Typecheck the code
	uv run basedpyright

.PHONY: typecheck
typecheck: typecheck-ts typecheck-py ## Typecheck all code

.PHONY: test
test: build ## Run tests and collect coverage data
	uv run coverage run -m pytest -v
	@uv run coverage report

.PHONY: all
all: format typecheck test ## run format, typecheck and test

.PHONY: help
help: ## Show this help (usage: make help)
	@echo "Usage: make [recipe]"
	@echo "Recipes:"
	@awk '/^[a-zA-Z0-9_-]+:.*?##/ { \
		helpMessage = match($$0, /## (.*)/); \
		if (helpMessage) { \
			recipe = $$1; \
			sub(/:/, "", recipe); \
			printf "  \033[36m%-20s\033[0m %s\n", recipe, substr($$0, RSTART + 3, RLENGTH); \
		} \
	}' $(MAKEFILE_LIST)
