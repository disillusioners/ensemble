# Makefile for agents-ensemble
# Build and install production version alongside dev

# Installation settings
INSTALL_DIR ?= $(HOME)/agents-ensemble
PROD_PORT ?= 8888

# Project structure
BACKEND_DIRS = daemon agents data
CONFIG_FILE = config.yaml
ENV_FILE = .env
FRONTEND_DIR = frontend
FRONTEND_DIST = frontend/dist/frontend/browser

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m

.PHONY: build install install-deps clean uninstall help

help:
	@echo "Available targets:"
	@echo "  make build         - Build the frontend"
	@echo "  make install       - Install production version to $(INSTALL_DIR)"
	@echo "  make install-deps  - Install Python dependencies in $(INSTALL_DIR)"
	@echo "  make clean         - Remove build artifacts"
	@echo "  make uninstall     - Remove installed production version"
	@echo ""
	@echo "Variables:"
	@echo "  INSTALL_DIR=$(INSTALL_DIR)"
	@echo "  PROD_PORT=$(PROD_PORT)"

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
		echo "$(GREEN)Using uv to create venv and install dependencies...$(NC)"; \
		uv venv && uv pip install -e .; \
	else \
		echo "$(GREEN)Using pip to create venv and install dependencies...$(NC)"; \
		python3 -m venv .venv && .venv/bin/pip install -e .; \
	fi
	@echo "$(GREEN)Dependencies installed!$(NC)"

# Install production version
install: build
	@echo "$(GREEN)Installing to $(INSTALL_DIR)...$(NC)"
	
	# Stop any process using the production port
	@echo "$(YELLOW)Checking for processes on port $(PROD_PORT)...$(NC)"
	@PID=$$(lsof -ti:$(PROD_PORT) 2>/dev/null) && { \
		echo "$(YELLOW)Stopping process $$PID on port $(PROD_PORT)...$(NC)"; \
		kill -9 $$PID 2>/dev/null || true; \
		sleep 1; \
	} || echo "$(GREEN)Port $(PROD_PORT) is available.$(NC)"
	
	# Create installation directory
	mkdir -p $(INSTALL_DIR)
	mkdir -p $(INSTALL_DIR)/data
	
	# Copy backend code
	@echo "$(YELLOW)Copying backend...$(NC)"
	cp -r daemon $(INSTALL_DIR)/
	
	# Copy agents directory
	@echo "$(YELLOW)Copying agents...$(NC)"
	cp -r agents $(INSTALL_DIR)/
	
	# Copy pyproject.toml and uv.lock for dependency installation
	@echo "$(YELLOW)Copying project files...$(NC)"
	cp pyproject.toml $(INSTALL_DIR)/
	cp README.md $(INSTALL_DIR)/ 2>/dev/null || echo "# Ensemble" > $(INSTALL_DIR)/README.md
	cp uv.lock $(INSTALL_DIR)/ 2>/dev/null || true
	
	# Copy and modify config.yaml with production port
	@echo "$(YELLOW)Configuring port $(PROD_PORT)...$(NC)"
	sed 's/port: \$${PORT:-8080}/port: \$${PORT:-$(PROD_PORT)}/' $(CONFIG_FILE) > $(INSTALL_DIR)/$(CONFIG_FILE)
	
	# Copy .env.prod file (or .env as fallback)
	@echo "$(YELLOW)Copying environment...$(NC)"
	cp .env.prod $(INSTALL_DIR)/.env 2>/dev/null || cp $(ENV_FILE) $(INSTALL_DIR)/.env 2>/dev/null || cp .env.example $(INSTALL_DIR)/.env 2>/dev/null || true
	
	# Copy frontend build (copy browser contents directly to dist)
	@echo "$(YELLOW)Copying frontend...$(NC)"
	mkdir -p $(INSTALL_DIR)/frontend/dist
	cp -r $(FRONTEND_DIST)/* $(INSTALL_DIR)/frontend/dist/ || echo "$(YELLOW)Warning: Frontend not built. Run 'make build' first.$(NC)"
	
	# Create venv and install dependencies
	@echo "$(YELLOW)Creating virtual environment and installing dependencies...$(NC)"
	cd $(INSTALL_DIR) && \
	if command -v uv >/dev/null 2>&1; then \
		echo "$(GREEN)Using uv...$(NC)"; \
		uv venv && uv pip install -e .; \
	else \
		echo "$(GREEN)Using pip...$(NC)"; \
		python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -e .; \
	fi
	
	# Create start script for production
	@echo "$(YELLOW)Creating production start script...$(NC)"
	@echo '#!/bin/bash' > $(INSTALL_DIR)/start.sh
	@echo '# Start Ensemble Daemon (Production)' >> $(INSTALL_DIR)/start.sh
	@echo 'set -e' >> $(INSTALL_DIR)/start.sh
	@echo 'cd "$$(dirname "$$0")"' >> $(INSTALL_DIR)/start.sh
	@echo '' >> $(INSTALL_DIR)/start.sh
	@echo '# Colors' >> $(INSTALL_DIR)/start.sh
	@echo 'GREEN="\033[0;32m"' >> $(INSTALL_DIR)/start.sh
	@echo 'NC="\033[0m"' >> $(INSTALL_DIR)/start.sh
	@echo '' >> $(INSTALL_DIR)/start.sh
	@echo '# Use venv python if available' >> $(INSTALL_DIR)/start.sh
	@echo 'if [ -f ".venv/bin/python" ]; then PYTHON=".venv/bin/python"; else PYTHON="python3"; fi' >> $(INSTALL_DIR)/start.sh
	@echo '' >> $(INSTALL_DIR)/start.sh
	@echo '# Load environment from .env' >> $(INSTALL_DIR)/start.sh
	@echo 'if [ -f ".env" ]; then export $$(cat .env | grep -v "^#" | xargs); fi' >> $(INSTALL_DIR)/start.sh
	@echo '' >> $(INSTALL_DIR)/start.sh
	@echo '# Set defaults' >> $(INSTALL_DIR)/start.sh
	@echo 'export PORT="$${PORT:-$(PROD_PORT)}"' >> $(INSTALL_DIR)/start.sh
	@echo 'export HOST="$${HOST:-0.0.0.0}"' >> $(INSTALL_DIR)/start.sh
	@echo 'mkdir -p data' >> $(INSTALL_DIR)/start.sh
	@echo '' >> $(INSTALL_DIR)/start.sh
	@echo 'echo -e "$${GREEN}Starting Ensemble Daemon...$${NC}"' >> $(INSTALL_DIR)/start.sh
	@echo 'echo "Port: $$PORT"' >> $(INSTALL_DIR)/start.sh
	@echo 'echo "API:  http://localhost:$$PORT/docs"' >> $(INSTALL_DIR)/start.sh
	@echo 'echo "UI:   http://localhost:$$PORT"' >> $(INSTALL_DIR)/start.sh
	@echo '' >> $(INSTALL_DIR)/start.sh
	@echo '$$PYTHON -m uvicorn daemon.api:app --host $$HOST --port $$PORT' >> $(INSTALL_DIR)/start.sh
	@chmod +x $(INSTALL_DIR)/start.sh
	
	@echo "$(GREEN)Installation complete!$(NC)"
	@echo ""
	@echo "Production version installed to: $(INSTALL_DIR)"
	@echo "Port: $(PROD_PORT)"
	@echo ""
	@echo "To start: cd $(INSTALL_DIR) && ./start.sh"
	@echo "API Docs: http://localhost:$(PROD_PORT)/docs"
	@echo "UI:       http://localhost:$(PROD_PORT)"

# Clean build artifacts
clean:
	@echo "$(GREEN)Cleaning build artifacts...$(NC)"
	rm -rf $(FRONTEND_DIR)/dist
	rm -rf $(FRONTEND_DIR)/.angular
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)Clean complete!$(NC)"

# Uninstall production version
uninstall:
	@echo "$(YELLOW)Removing $(INSTALL_DIR)...$(NC)"
	rm -rf $(INSTALL_DIR)
	@echo "$(GREEN)Uninstall complete!$(NC)"
