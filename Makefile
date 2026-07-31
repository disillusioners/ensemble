# Makefile for agents-ensemble
# Build and install production version alongside dev

# Installation settings
INSTALL_DIR ?= $(HOME)/agents-ensemble
PROD_PORT ?= 9797

# Project structure
BACKEND_DIRS = daemon agents data
CONFIG_FILE = config.yaml
ENV_FILE = .env
ENV_PROD_FILE = .env.prod
FRONTEND_DIR = frontend
FRONTEND_DIST = frontend/dist/frontend/browser

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

.PHONY: build install install-deps clean uninstall help sync stop start dev pyinstaller pyinstaller-clean

help:
	@echo "Available targets:"
	@echo "  make build           - Build the frontend"
	@echo "  make pyinstaller     - Build binary (dist/ensemble-prod, clears dist first)"
	@echo "  make install         - Build and install to $(INSTALL_DIR) (backs up existing binary)"
	@echo "  make install-deps    - Install Python dependencies in $(INSTALL_DIR)"
	@echo "  make sync            - Install dependencies with uv sync"
	@echo "  make start           - Start the daemon (kills existing process first)"
	@echo "  make stop            - Stop the daemon"
	@echo "  make dev             - stop + sync + start"
	@echo "  make clean           - Remove build artifacts"
	@echo "  make pyinstaller-clean - Remove PyInstaller build files"
	@echo "  make uninstall       - Remove installed production version"
	@echo ""
	@echo "Variables:"
	@echo "  INSTALL_DIR=$(INSTALL_DIR)"
	@echo "  PROD_PORT=$(PROD_PORT)"

# Sync dependencies with uv
sync:
	@echo "$(GREEN)Syncing dependencies (incl. dev extras for pytest-timeout)...$(NC)"
	uv sync --extra dev
	@echo "$(GREEN)Dependencies synced!$(NC)"

# Stop the daemon
stop:
	@echo "$(YELLOW)Stopping daemon on port $(PROD_PORT)...$(NC)"
	@PID=$$(lsof -ti:$(PROD_PORT) 2>/dev/null) && { \
		kill $$PID 2>/dev/null && echo "$(GREEN)Stopped process $$PID$(NC)" || true; \
	} || echo "$(GREEN)No process found on port $(PROD_PORT)$(NC)"

# Start the daemon
start: stop
	./start.sh

# Development workflow
dev: stop sync start

# Build frontend
build:
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
	
	# Stop any process using the production port
	@echo "$(YELLOW)Checking for processes on port $(PROD_PORT)...$(NC)"
	@PID=$$(lsof -ti:$(PROD_PORT) 2>/dev/null) && { \
		echo "$(YELLOW)Stopping process $$PID on port $(PROD_PORT)...$(NC)"; \
		kill -9 $$PID 2>/dev/null || true; \
		sleep 1; \
	} || echo "$(GREEN)Port $(PROD_PORT) is available.$(NC)"
	
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
	
	# Copy and modify config.yaml with production port
	@echo "$(YELLOW)Configuring port $(PROD_PORT)...$(NC)"
	sed 's/port: \$${PORT:-8079}/port: \$${PORT:-$(PROD_PORT)}/' $(CONFIG_FILE) > $(INSTALL_DIR)/$(CONFIG_FILE)
	
	# Copy .env.prod file (or .env as fallback)
	@echo "$(YELLOW)Copying environment...$(NC)"
	cp $(ENV_PROD_FILE) $(INSTALL_DIR)/.env 2>/dev/null || \
		cp $(ENV_FILE) $(INSTALL_DIR)/.env 2>/dev/null || \
		cp .env.example $(INSTALL_DIR)/.env 2>/dev/null || true
	
	# Copy frontend build (preserve frontend/dist/frontend/browser structure)
	@echo "$(YELLOW)Copying frontend...$(NC)"
	rm -rf $(INSTALL_DIR)/frontend/dist
	mkdir -p $(INSTALL_DIR)/frontend/dist/frontend/browser
	cp -r $(FRONTEND_DIST)/* $(INSTALL_DIR)/frontend/dist/frontend/browser/ || echo "$(YELLOW)Warning: Frontend not built. Run 'make build' first.$(NC)"
	
	@echo "$(GREEN)Installation complete!$(NC)"
	@echo ""
	@echo "Production version installed to: $(INSTALL_DIR)"
	@echo "Binary: ensemble-prod"
	@echo "Port: $(PROD_PORT)"
	@echo ""
	@echo "To start: cd $(INSTALL_DIR) && ./ensemble-prod"
	@echo "API Docs: http://localhost:$(PROD_PORT)/docs"
	@echo "UI:       http://localhost:$(PROD_PORT)"
	@echo ""
	@echo "Required files in working directory:"
	@echo "  - config.yaml"
	@echo "  - .env"
	@echo "  - agents/"
	@echo "  - frontend/dist/"
	@echo "  - data/"

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
