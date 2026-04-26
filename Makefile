SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down clean demo demo-fresh cli cli-example query logs ps \
        scenario scenario-list test test-unit test-scenarios test-smoke \
        lint format

help: ## Show targets
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[1;36m%-20s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

demo: ## End-to-end scripted demo: stack up → browser → scenarios → results
	@./scripts/demo.sh

demo-fresh: ## Same as demo, but wipes state first (forces license re-validation)
	@./scripts/demo.sh --fresh

up: ## Prompt for email (if needed), write .env, then bring the stack up
	@./scripts/setup.sh
	@echo
	@echo "============================================================="
	@echo "  InfluxDB 3 Enterprise is starting."
	@echo "  Check the email you provided and CLICK THE VALIDATION LINK."
	@echo "  The simulator and UI will start automatically once validation"
	@echo "  completes. UI: http://localhost:8080   API: http://localhost:8181"
	@echo "============================================================="
	@$(COMPOSE) up -d

down: ## Stop services (preserves data volume)
	@$(COMPOSE) down

clean: ## Stop services and drop the data volume (requires re-validation next time)
	@$(COMPOSE) down -v

logs: ## Tail all service logs
	@$(COMPOSE) logs -f

ps: ## Show service status
	@$(COMPOSE) ps

cli: ## Shell into influxdb3 container; TOKEN is exported, `iql <sql>` runs queries
	@$(COMPOSE) exec influxdb3 bash -c '\
	  export TOKEN=$$(cat /var/lib/influxdb3/.iiot-token-plain); \
	  iql() { influxdb3 query --database iiot --token "$$TOKEN" "$$1"; }; \
	  export -f iql; \
	  echo ""; \
	  echo "  TOKEN is exported. Try:"; \
	  echo "    iql \"SELECT COUNT(*) FROM machine_state\""; \
	  echo "    iql \"SELECT * FROM machine_state ORDER BY time DESC LIMIT 5\""; \
	  echo ""; \
	  exec bash'

query: ## One-shot query. Usage: make query sql='SELECT COUNT(*) FROM machine_state'
	@test -n "$(sql)" || (echo "usage: make query sql='<SQL>'"; exit 1)
	@$(COMPOSE) exec -T -e "SQL=$(sql)" influxdb3 bash -c 'TOKEN=$$(cat /var/lib/influxdb3/.iiot-token-plain); influxdb3 query --database iiot --token "$$TOKEN" "$$SQL"'

cli-example: ## Run a named curated CLI example. Usage: make cli-example name=list-databases
	@test -n "$(name)" || (echo "usage: make cli-example name=<example>"; exit 1)
	@grep -A 20 "^## $(name)" CLI_EXAMPLES.md | sed -n '/^```bash/,/^```/p' | sed '1d;$$d' \
	  | while read -r line; do echo "+ $$line"; $(COMPOSE) exec -T influxdb3 bash -lc "export TOKEN=\$$(cat /var/lib/influxdb3/.iiot-token-plain); $$line"; done

scenario: ## Run a scenario. Usage: make scenario name=unplanned_downtime_cascade
	@test -n "$(name)" || (echo "usage: make scenario name=<scenario>"; exit 1)
	@SCENARIO=$(name) $(COMPOSE) --profile scenarios run --rm scenarios

scenario-list: ## List available scenarios
	@ls simulator/scenarios/*.py 2>/dev/null | grep -v __init__ | xargs -I{} basename {} .py | while read n; do \
	  desc=$$(grep -m1 '^"""' simulator/scenarios/$$n.py | sed 's/"""//g'); \
	  printf "  %-32s %s\n" "$$n" "$$desc"; done

test: test-unit test-scenarios ## Run unit + scenario tests (skip smoke)

test-unit: ## Plugin + signal + query unit tests (no docker)
	@pytest tests -q -m "not scenario and not smoke"

test-scenarios: ## Scenario integration tests (uses testcontainers)
	@pytest tests/test_scenarios -q -m scenario

test-smoke: ## End-to-end smoke via docker compose (slow)
	@pytest tests/test_smoke.py -q -m smoke

lint: ## Check formatting and lint
	@ruff check .
	@ruff format --check .

format: ## Auto-fix formatting
	@ruff check --fix .
	@ruff format .
