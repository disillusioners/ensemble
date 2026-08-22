# Makefile for agents-ensemble
# Build and install production version alongside dev

# Installation settings
# PROD_PORT was retired per ADR-014 (D1 FINAL): prod port comes from
# .env.prod staged to INSTALL_DIR/.env (PORT=9797 canonical); the broken
# config.yaml sed is gone (see install target). Operator-facing stop/start
# echo+kill paths use PROD_PORT_FALLBACK (defined below, AFTER ENV_PROD_FILE
# so the := shell expansion actually sees the file name), sourced from .env.prod.
INSTALL_DIR ?= $(HOME)/agents-ensemble

# Project structure
BACKEND_DIRS = daemon agents data
CONFIG_FILE = config.yaml
ENV_PROD_FILE = .env.prod
FRONTEND_DIR = frontend
FRONTEND_DIST = frontend/dist/frontend/browser

# Port actually in use by the installed prod daemon: read from .env.prod
# (PORT=…) — the real port source per ADR-014. Defined AFTER ENV_PROD_FILE on
# purpose: := expands immediately, so the ordering is load-bearing. Empty-safe
# (fresh clone, no .env.prod yet → falls back to the canonical 9797).
# `tr -d '\r'` — .env.prod written on Windows/editors with CRLF leaves a
# trailing \r on the PORT= line; sed `s/^PORT=//p` keeps it and the \r then
# breaks lsof (`lsof -ti:9797\r`) and every echoed URL (review m7).
PROD_PORT_FALLBACK := $(shell sed -n 's/^PORT=//p' $(ENV_PROD_FILE) 2>/dev/null | tr -d '\r' | head -1)
PROD_PORT_FALLBACK := $(if $(PROD_PORT_FALLBACK),$(PROD_PORT_FALLBACK),9797)

# PyInstaller settings
PYINSTALLER_SPEC = ensemble.spec
# Binary name
BINARY_NAME = ensemble-prod
BACKUP_NAME = backup-$(BINARY_NAME)-$(shell date +%Y%m%d-%H%M%S).bak
DATA_BACKUP_DIR = data-backup/data-$(shell date +%Y%m%d-%H%M%S)

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m

.PHONY: build install install-deps clean uninstall help sync stop stop-by-port start dev pyinstaller pyinstaller-clean ensure-latest plist-install watchdog-install deploy-demo deploy-live stage-demo

help:
	@echo "Available targets:"
	@echo "  make build           - Build the frontend"
	@echo "  make pyinstaller     - Build binary (dist/ensemble-prod, clears dist first)"
	@echo "  make install         - Build and install to $(INSTALL_DIR) (backs up existing binary)"
	@echo "  make plist-install   - Copy staged plist to ~/Library/LaunchAgents (prints launchctl hint)"
	@echo "  make watchdog-install - Install the watchdog-watcher launchd agent (m3)"
	@echo "  make install-deps    - Install Python dependencies in $(INSTALL_DIR)"
	@echo "  make sync            - Install dependencies with uv sync"
	@echo "  make start           - Start the daemon (dev flow: stop + start.sh)"
	@echo "  make stop            - Stop ONLY processes owned by $(INSTALL_DIR) (ownership-scoped; never port-based)"
	@echo "  make stop-by-port    - OPT-IN legacy port kill (unsafe on coexistence hosts)"
	@echo "  make deploy-demo     - Deploy to the DEMO env (~/agents-ensemble-demo, :7979, ensemble_demo)"
	@echo "  make deploy-live     - Deploy to LIVE (~/agents-ensemble, :9797) — needs ENSEMBLE_DEPLOY_LIVE=1"
	@echo "  make stage-demo      - Deploy to demo WITHOUT starting it (stage only)"
	@echo "  make dev             - stop + sync + start"
	@echo "  make clean           - Remove build artifacts"
	@echo "  make pyinstaller-clean - Remove PyInstaller build files"
	@echo "  make uninstall       - Remove installed production version"
	@echo ""
	@echo "Variables:"
	@echo "  INSTALL_DIR=$(INSTALL_DIR)"
	@echo "  PROD_PORT_FALLBACK=$(PROD_PORT_FALLBACK) (read from $(ENV_PROD_FILE); real port source is .env.prod per ADR-014)"

# Sync dependencies with uv
sync:
	@echo "$(GREEN)Syncing dependencies (incl. dev extras for pytest-timeout)...$(NC)"
	uv sync --extra dev
	@echo "$(GREEN)Dependencies synced!$(NC)"

# Stop the daemon — OWNERSHIP-SCOPED (incident fix, 2026-08-16).
# `make stop` used to SIGTERM whatever listened on the prod port; on this
# dev+prod coexistence host that is structurally unsafe (it killed the real
# prod — the port is even shared with unrelated clients like Chrome's
# network service). Now ALL stopping is delegated to
# scripts/stop-ensemble.sh, which only ever signals processes OWNED by
# $(INSTALL_DIR):
#   * anchored <INSTALL_DIR>/launcher.sh / ensemble-prod /
#     current/ensemble-prod command-line paths, or
#   * an ensemble-shaped process whose cwd IS the install dir (the
#     relative `./ensemble-prod` start form).
# Port lookups are reporting hints only. Legacy port-kill remains behind
# an explicit opt-in (STOP_BY_PORT=1 make stop) with a loud warning.
# The ownership script also prefers stopping the LAUNCHER first — a
# supervisor that receives SIGTERM forwards it to the daemon child and
# exits with its real exit code, so a stop is never classified as a
# crash and nothing bounces back up (review m5, ownership-redesigned).
STOP_SCRIPT = scripts/stop-ensemble.sh
stop:
	@bash $(STOP_SCRIPT) $(INSTALL_DIR) $(PROD_PORT_FALLBACK)

.PHONY: stop-by-port
stop-by-port:
	@echo "$(RED)STOP_BY_PORT: killing by port $(PROD_PORT_FALLBACK) can terminate UNRELATED listeners$(NC)"
	@echo "$(RED)(dev+prod coexistence hosts share ports with clients — e.g. browsers). You opted in.$(NC)"
	STOP_BY_PORT=1 bash $(STOP_SCRIPT) $(INSTALL_DIR) $(PROD_PORT_FALLBACK)

# Start the daemon
start: stop
	./start.sh

# Development workflow
dev: stop sync start

# Ensure we are on the latest branch before any build/install
ensure-latest:
	@echo "$(YELLOW)Switching to latest branch...$(NC)"
	git checkout latest
	git pull

# Build frontend
build: ensure-latest
	@echo "$(GREEN)Building frontend...$(NC)"
	cd $(FRONTEND_DIR) && npm install && npm run build
	@echo "$(GREEN)Build complete!$(NC)"

# Install Python dependencies in production directory
install-deps:
	@echo "$(GREEN)Installing Python dependencies in $(INSTALL_DIR)...$(NC)"
	@if [ ! -d "$(INSTALL_DIR)" ]; then \
		echo "$(RED)Error: $(INSTALL_DIR) does not exist. Run 'make install' first.$(NC)"; \
		exit 1; \
	fi
	cd $(INSTALL_DIR) && \
	if command -v uv >/dev/null 2>&1; then \
		echo "$(GREEN)Using uv to sync dependencies...$(NC)"; \
		uv sync; \
	else \
		echo "$(GREEN)Using pip to create venv and install dependencies...$(NC)"; \
		python3 -m venv .venv && .venv/bin/pip install -e .; \
	fi
	@echo "$(GREEN)Dependencies installed!$(NC)"

# Install production version
install: pyinstaller
	@echo "$(GREEN)Installing to $(INSTALL_DIR)...$(NC)"
	
	# Stop only what THIS install owns — ownership-scoped, never port-based
	# (incident fix 2026-08-16; see scripts/stop-ensemble.sh). A foreign
	# daemon on the same port (dev+prod coexistence) is reported, not killed.
	@echo "$(YELLOW)Stopping processes owned by $(INSTALL_DIR) (ownership-scoped)...$(NC)"
	@bash $(STOP_SCRIPT) $(INSTALL_DIR) $(PROD_PORT_FALLBACK) || true
	
	# Clean up old backups (keep only 2 most recent)
	@echo "$(YELLOW)Cleaning up old backups...$(NC)"; \
	cd $(INSTALL_DIR) && { \
		ls -dt backup-$(BINARY_NAME)-*.bak 2>/dev/null | tail -n +3 | xargs rm -f 2>/dev/null || true; \
		ls -dt data-backup/data-* 2>/dev/null | tail -n +3 | xargs rm -rf 2>/dev/null || true; \
	}; \
	echo "$(GREEN)Cleanup complete.$(NC)"
	
	# Backup existing binary if present
	@if [ -f "$(INSTALL_DIR)/$(BINARY_NAME)" ]; then \
		echo "$(YELLOW)Backing up existing $(BINARY_NAME)...$(NC)"; \
		mv $(INSTALL_DIR)/$(BINARY_NAME) $(INSTALL_DIR)/$(BACKUP_NAME); \
		echo "$(GREEN)Backed up to $(BACKUP_NAME)$(NC)"; \
	else \
		echo "$(GREEN)No existing binary to backup.$(NC)"; \
	fi
	
	# Backup only what is needed from the existing data directory.
	# PostgreSQL holds the real state (instances + checkpoints) when
	# ensemble.json selects "postgres"; the on-disk SQLite DBs there are
	# stale, so copying them (especially checkpoints.db, often many GB)
	# is wasteful. We always back up ensemble.json (backend selector +
	# runtime config) and opencode_sessions.db (always a local SQLite
	# file, never migrated to postgres). In sqlite mode we additionally
	# back up instances.db and checkpoints.db with their WAL/SHM sidecars.
	@if [ -d "$(INSTALL_DIR)/data" ] && [ "$$(ls -A $(INSTALL_DIR)/data 2>/dev/null)" ]; then \
		echo "$(YELLOW)Backing up existing data directory (selective)...$(NC)"; \
		mkdir -p $(INSTALL_DIR)/$(DATA_BACKUP_DIR); \
		if command -v python3 >/dev/null 2>&1 && [ -f "$(INSTALL_DIR)/data/ensemble.json" ]; then \
			BACKEND=$$(python3 -c "import json,sys; print(json.load(open('$(INSTALL_DIR)/data/ensemble.json')).get('database','sqlite'))" 2>/dev/null || echo sqlite); \
		else \
			BACKEND=sqlite; \
		fi; \
		echo "  detected backend: $$BACKEND"; \
		FILES="ensemble.json opencode_sessions.db opencode_sessions.db-wal opencode_sessions.db-shm"; \
		if [ "$$BACKEND" = "sqlite" ]; then \
			FILES="$$FILES instances.db instances.db-wal instances.db-shm checkpoints.db checkpoints.db-wal checkpoints.db-shm"; \
		fi; \
		for f in $$FILES; do \
			if [ -f "$(INSTALL_DIR)/data/$$f" ]; then \
				cp -p $(INSTALL_DIR)/data/$$f $(INSTALL_DIR)/$(DATA_BACKUP_DIR)/; \
				echo "  backed up $$f"; \
			fi; \
		done; \
		echo "$(GREEN)Backed up to $(DATA_BACKUP_DIR)$(NC)"; \
	else \
		echo "$(GREEN)No existing data to backup.$(NC)"; \
	fi
	
	# Create installation directory
	mkdir -p $(INSTALL_DIR)
	mkdir -p $(INSTALL_DIR)/data
	
	# Copy binary
	@echo "$(YELLOW)Installing $(BINARY_NAME)...$(NC)"
	cp dist/$(BINARY_NAME) $(INSTALL_DIR)/$(BINARY_NAME)
	chmod +x $(INSTALL_DIR)/$(BINARY_NAME)
	
	# Remove daemon folder if exists (binary is self-contained, local daemon would shadow bundled code)
	@if [ -d "$(INSTALL_DIR)/daemon" ]; then \
		echo "$(YELLOW)Removing old daemon folder (binary is self-contained)...$(NC)"; \
		rm -rf "$(INSTALL_DIR)/daemon"; \
	fi
	
	# Copy agents directory (clean copy)
	@echo "$(YELLOW)Copying agents...$(NC)"
	rm -rf $(INSTALL_DIR)/agents
	cp -r $(CURDIR)/agents $(INSTALL_DIR)/agents
	
	# Copy config.yaml AS-IS (no port munging — the old sed targeting
	# 'port: ${PORT:-8079}' never matched the actual 'port: ${PORT:-8088}'
	# default and is retired per ADR-014: the prod port comes from .env.prod
	# staged to INSTALL_DIR/.env, which config.yaml's ${PORT:-…} interpolation
	# resolves from the exported environment)
	@echo "$(YELLOW)Copying config.yaml (unmodified)...$(NC)"
	cp $(CONFIG_FILE) $(INSTALL_DIR)/$(CONFIG_FILE)
	
	# Stage env: repo .env.prod → INSTALL_DIR/.env — the single canonical
	# prod env source (ADR-014). No .env / .env.example fallback anymore:
	# staging a dev env into prod silently was a misconfiguration risk;
	# prod requires explicit intent. Override knob: ENV_PROD_FILE.
	@echo "$(YELLOW)Staging $(ENV_PROD_FILE) → $(INSTALL_DIR)/.env...$(NC)"
	@if [ ! -f "$(ENV_PROD_FILE)" ]; then \
		echo "$(RED)Error: $(ENV_PROD_FILE) not found in repo root.$(NC)"; \
		echo "$(RED)Prod install requires an explicit prod env file (ADR-014).$(NC)"; \
		echo "$(RED)Create it from the tracked template:  cp .env.prod.example .env.prod$(NC)"; \
		echo "$(RED)then set PORT=9797 (canonical prod port) and your prod values.$(NC)"; \
		exit 1; \
	fi
	cp $(ENV_PROD_FILE) $(INSTALL_DIR)/.env
	
	# Stage the launchd plist with the real install path substituted
	# (template lives in scripts/ensemble-prod.plist with placeholders).
	# Literal substitution (review m8): the old `sed 's|…|$(INSTALL_DIR)|g'`
	# — and equally an `awk -v rep=…` + gsub form — deliberately NOT used —
	# reinterprets `&` (expands to the matched placeholder) and `\` in
	# INSTALL_DIR. This awk uses ENVIRON (no -v unescaping) + index/substr
	# splicing, which is byte-literal. Proven with INSTALL_DIR='/tmp/a&b\|c'
	# → placeholder replaced by exactly that string. The redirect is quoted
	# for the same reason (shell split/glob safety).
	@echo "$(YELLOW)Staging launcher + plist...$(NC)"
	cp $(CURDIR)/launcher.sh $(INSTALL_DIR)/launcher.sh
	chmod +x $(INSTALL_DIR)/launcher.sh
	mkdir -p $(INSTALL_DIR)/data
	INSTALL_DIR='$(INSTALL_DIR)' awk 'BEGIN { rep = ENVIRON["INSTALL_DIR"]; p = "INSTALL_DIR_PLACEHOLDER"; lp = length(p) } { while ((i = index($$0, p)) > 0) $$0 = substr($$0, 1, i - 1) rep substr($$0, i + lp); print }' scripts/ensemble-prod.plist \
		> "$(INSTALL_DIR)/data/ensemble-prod.plist"
	# Watchdog-watcher (m3): stage the script + its launchd-agent plist
	# (same literal-substitution awk as above — no sed `&` hazards).
	mkdir -p $(INSTALL_DIR)/scripts
	cp $(CURDIR)/scripts/watchdog-watcher.sh $(INSTALL_DIR)/scripts/watchdog-watcher.sh
	chmod +x $(INSTALL_DIR)/scripts/watchdog-watcher.sh
	INSTALL_DIR='$(INSTALL_DIR)' awk 'BEGIN { rep = ENVIRON["INSTALL_DIR"]; p = "INSTALL_DIR_PLACEHOLDER"; lp = length(p) } { while ((i = index($$0, p)) > 0) $$0 = substr($$0, 1, i - 1) rep substr($$0, i + lp); print }' scripts/ensemble-watchdog-watcher.plist \
		> "$(INSTALL_DIR)/data/ensemble-watchdog-watcher.plist"
	
	# Copy frontend build (preserve frontend/dist/frontend/browser structure)
	@echo "$(YELLOW)Copying frontend...$(NC)"
	rm -rf $(INSTALL_DIR)/frontend/dist
	mkdir -p $(INSTALL_DIR)/frontend/dist/frontend/browser
	cp -r $(FRONTEND_DIST)/* $(INSTALL_DIR)/frontend/dist/frontend/browser/ || echo "$(YELLOW)Warning: Frontend not built. Run 'make build' first.$(NC)"
	
	@echo "$(GREEN)Installation complete!$(NC)"
	@echo ""
	@echo "Production version installed to: $(INSTALL_DIR)"
	@echo "Binary: ensemble-prod"
	@echo "Port: $(PROD_PORT_FALLBACK) (from $(ENV_PROD_FILE) → $(INSTALL_DIR)/.env, ADR-014)"
	@echo ""
	@echo "To start (launchd):  make plist-install   # then bootstrap per its hint"
	@echo "To start (manual):   cd $(INSTALL_DIR) && ./launcher.sh"
	@echo "API Docs: http://localhost:$(PROD_PORT_FALLBACK)/docs"
	@echo "UI:       http://localhost:$(PROD_PORT_FALLBACK)"
	@echo "Health:   http://localhost:$(PROD_PORT_FALLBACK)/livez and /readyz"
	@echo ""
	@echo "Required files in working directory:"
	@echo "  - config.yaml"
	@echo "  - .env"
	@echo "  - agents/"
	@echo "  - frontend/dist/"
	@echo "  - data/"

# Copy the staged plist (INSTALL_DIR/data/ensemble-prod.plist, path already
# sed'd by `make install`) into ~/Library/LaunchAgents and print the
# bootstrap hint. Convenience only — the operator runs launchctl by hand.
plist-install:
	@if [ ! -f "$(INSTALL_DIR)/data/ensemble-prod.plist" ]; then \
		echo "$(RED)Error: $(INSTALL_DIR)/data/ensemble-prod.plist not found — run 'make install' first.$(NC)"; \
		exit 1; \
	fi
	mkdir -p $(HOME)/Library/LaunchAgents
	cp $(INSTALL_DIR)/data/ensemble-prod.plist $(HOME)/Library/LaunchAgents/ensemble-prod.plist
	@echo "$(GREEN)Plist installed: $(HOME)/Library/LaunchAgents/ensemble-prod.plist$(NC)"
	@echo ""
	@echo "To load it (macOS launchd):"
	@echo "  launchctl bootstrap gui/$$(id -u) $(HOME)/Library/LaunchAgents/ensemble-prod.plist"
	@echo ""
	@echo "To unload:"
	@echo "  launchctl bootout gui/$$(id -u)/com.ensemble.prod"
	@echo ""
	@echo "Logs: $(INSTALL_DIR)/data/launcher.log (stdout) + launcher.err.log (stderr)"

# Watchdog-watcher launchd AGENT (Auto-Restart Phase 1, m3): stages the
# plist with the real path (if missing) and copies it to
# ~/Library/LaunchAgents. Watches /livez every 300s; notifies when the
# daemon has been absent >600s. Observation only — never restarts.
# Demo-env variant: hand-duplicate the plist with Label
# com.ensemble.watchdog.demo + the demo INSTALL_DIR (see the template's
# DEMO USAGE note) — same convention as ensemble-prod.plist.
watchdog-install:
	@if [ ! -f "$(INSTALL_DIR)/data/ensemble-watchdog-watcher.plist" ]; then \
		if [ -f "$(INSTALL_DIR)/scripts/watchdog-watcher.sh" ]; then \
			INSTALL_DIR='$(INSTALL_DIR)' awk 'BEGIN { rep = ENVIRON["INSTALL_DIR"]; p = "INSTALL_DIR_PLACEHOLDER"; lp = length(p) } { while ((i = index($$0, p)) > 0) $$0 = substr($$0, 1, i - 1) rep substr($$0, i + lp); print }' $(CURDIR)/scripts/ensemble-watchdog-watcher.plist > "$(INSTALL_DIR)/data/ensemble-watchdog-watcher.plist"; \
		else \
			echo "$(RED)Error: $(INSTALL_DIR)/scripts/watchdog-watcher.sh not found — run 'make install' first.$(NC)"; \
			exit 1; \
		fi; \
	fi
	mkdir -p $(HOME)/Library/LaunchAgents
	cp $(INSTALL_DIR)/data/ensemble-watchdog-watcher.plist $(HOME)/Library/LaunchAgents/ensemble-watchdog-watcher.plist
	@echo "$(GREEN)Watchdog-watcher agent installed: $(HOME)/Library/LaunchAgents/ensemble-watchdog-watcher.plist$(NC)"
	@echo ""
	@echo "To load it (macOS launchd):"
	@echo "  launchctl bootstrap gui/$$(id -u) $(HOME)/Library/LaunchAgents/ensemble-watchdog-watcher.plist"
	@echo ""
	@echo "To unload:"
	@echo "  launchctl bootout gui/$$(id -u)/com.ensemble.watchdog"
	@echo ""
	@echo "Logs: $(INSTALL_DIR)/data/watchdog.log (notifies after 600s of /livez absence)"

# Deploy targets (Auto-Restart Phase 1, 3-env topology: dev 8079 / demo 7979 /
# live 9797). Thin wrappers over scripts/deploy.sh — all logic (config table,
# guards, ownership-scoped stop, health gate, live-PID assertion) lives there.
# deploy-live additionally demands ENSEMBLE_DEPLOY_LIVE=1 (deploy.sh refuses
# otherwise — live is the running orchestrator, read-only until the feature
# is done). Extra flags pass through, e.g.: make deploy-demo ARGS="--build".
DEPLOY_SCRIPT = scripts/deploy.sh
deploy-demo:
	bash $(DEPLOY_SCRIPT) demo $(ARGS)

deploy-live:
	bash $(DEPLOY_SCRIPT) live $(ARGS)

stage-demo:
	bash $(DEPLOY_SCRIPT) demo --no-start $(ARGS)

# Clean build artifacts
clean:
	@echo "$(GREEN)Cleaning build artifacts...$(NC)"
	rm -rf $(FRONTEND_DIR)/dist
	rm -rf $(FRONTEND_DIR)/.angular
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)Clean complete!$(NC)"

# PyInstaller build targets
pyinstaller-clean:
	@echo "$(GREEN)Cleaning PyInstaller artifacts...$(NC)"
	rm -rf build/ dist/
	@echo "$(GREEN)PyInstaller clean complete!$(NC)"

pyinstaller: build
	@echo "$(GREEN)Building production binary with PyInstaller...$(NC)"
	@echo "$(YELLOW)Clearing build and dist directories...$(NC)"
	rm -rf build/ dist/
	uv run python -m PyInstaller $(PYINSTALLER_SPEC)
	@echo "$(GREEN)Binary built: dist/$(BINARY_NAME)$(NC)"

# Uninstall production version
uninstall:
	@echo "$(YELLOW)Removing $(INSTALL_DIR)...$(NC)"
	rm -rf $(INSTALL_DIR)
	@echo "$(GREEN)Uninstall complete!$(NC)"
