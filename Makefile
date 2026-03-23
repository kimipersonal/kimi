PROJECT_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
BACKEND_DIR := $(PROJECT_DIR)/backend
FRONTEND_DIR := $(PROJECT_DIR)/frontend
LOG_DIR := $(PROJECT_DIR)/logs
VENV := $(BACKEND_DIR)/.venv/bin
PID_DIR := $(LOG_DIR)

.PHONY: start stop clean status build help

help: ## Show this help
	@echo "AI Holding — Multi-Agent Company System"
	@echo ""
	@echo "Usage:  make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ============================================================================
# START
# ============================================================================
start: ## Start everything (DB, backend, frontend, nginx, tunnel)
	@echo "=== AI Holding — Starting ==="
	@mkdir -p $(LOG_DIR)

	@# --- 1. Database services (Docker) ---
	@echo "[1/6] Starting PostgreSQL + Redis..."
	@docker compose -f $(PROJECT_DIR)/docker-compose.yml up -d postgres redis 2>/dev/null || true
	@for i in $$(seq 1 20); do \
		(pg_isready -h localhost -p 5432 -q 2>/dev/null || ss -tlnp 2>/dev/null | grep -q ':5432 ') && break; \
		sleep 1; \
	done
	@echo "  ✓ Database services ready"

	@# --- 2. Build frontend ---
	@echo "[2/6] Building frontend..."
	@cd $(FRONTEND_DIR) && npm run build > $(LOG_DIR)/frontend-build.log 2>&1
	@echo "  ✓ Frontend built"

	@# --- 3. Kill old processes ---
	@echo "[3/6] Cleaning up old processes..."
	@$(PROJECT_DIR)/scripts/stop.sh 2>/dev/null || true

	@# --- 4. Nginx ---
	@echo "[4/6] Starting nginx..."
	@sudo ln -sf $(PROJECT_DIR)/nginx/ai-holding.conf /etc/nginx/sites-enabled/ai-holding.conf
	@sudo rm -f /etc/nginx/sites-enabled/default
	@sudo nginx -t 2>/dev/null
	@sudo systemctl start nginx
	@echo "  ✓ Nginx on port 8080"

	@# --- 5. Backend ---
	@echo "[5/6] Starting backend..."
	@cd $(BACKEND_DIR) && \
		set -a && . $(PROJECT_DIR)/.env 2>/dev/null && set +a && \
		$(VENV)/python -m uvicorn app.main:app \
			--host 0.0.0.0 --port 8000 \
			> $(LOG_DIR)/backend.log 2>&1 & \
		echo $$! > $(PID_DIR)/backend.pid
	@for i in $$(seq 1 30); do \
		curl -sf http://localhost:8000/health > /dev/null 2>&1 && break; \
		sleep 1; \
	done
	@echo "  ✓ Backend started (PID: $$(cat $(PID_DIR)/backend.pid))"

	@# --- 6. Frontend ---
	@echo "[6/6] Starting frontend..."
	@cd $(FRONTEND_DIR) && \
		npx next start --port 3000 \
			> $(LOG_DIR)/frontend.log 2>&1 & \
		echo $$! > $(PID_DIR)/frontend.pid
	@for i in $$(seq 1 15); do \
		curl -sf http://localhost:3000 > /dev/null 2>&1 && break; \
		sleep 1; \
	done
	@echo "  ✓ Frontend started (PID: $$(cat $(PID_DIR)/frontend.pid))"

	@# --- Summary ---
	@echo ""
	@echo "=== AI Holding is Running ==="
	@sleep 3
	@echo "Backend:   http://localhost:8000"
	@echo "Frontend:  http://localhost:3000"
	@echo "Nginx:     http://localhost:8080"
	@TUNNEL_URL=$$(cat $(PROJECT_DIR)/.cloudflare_url 2>/dev/null); \
	if [ -n "$$TUNNEL_URL" ]; then \
		echo "Tunnel:    $$TUNNEL_URL"; \
	else \
		echo "Tunnel:    Starting... use /web in Telegram"; \
	fi
	@echo ""
	@echo "Logs:      $(LOG_DIR)/"
	@echo "Stop:      make stop"

# ============================================================================
# STOP
# ============================================================================
stop: ## Stop everything (backend, frontend, tunnel, nginx)
	@echo "=== AI Holding — Stopping ==="
	@$(PROJECT_DIR)/scripts/stop.sh
	@echo ""
	@echo "=== All services stopped ==="
	@echo "Note: PostgreSQL + Redis still running (make db-stop to stop)"

# ============================================================================
# CLEAN
# ============================================================================
clean: ## Clean caches (keeps DB data and source code safe)
	@echo "=== Cleaning caches ==="

	@# Python bytecode
	@find $(BACKEND_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find $(BACKEND_DIR) -name "*.pyc" -delete 2>/dev/null || true
	@find $(BACKEND_DIR) -name "*.pyo" -delete 2>/dev/null || true
	@echo "  ✓ Python __pycache__ cleaned"

	@# mypy cache
	@rm -rf $(BACKEND_DIR)/.mypy_cache 2>/dev/null || true
	@echo "  ✓ mypy cache cleaned"

	@# pytest cache
	@rm -rf $(BACKEND_DIR)/.pytest_cache 2>/dev/null || true
	@echo "  ✓ pytest cache cleaned"

	@# Next.js build cache
	@rm -rf $(FRONTEND_DIR)/.next 2>/dev/null || true
	@echo "  ✓ Next.js .next/ cleaned"

	@# Logs
	@rm -rf $(LOG_DIR)/*.log 2>/dev/null || true
	@echo "  ✓ Logs cleaned"

	@# Tunnel URL file
	@rm -f $(PROJECT_DIR)/.cloudflare_url 2>/dev/null || true

	@# PID files
	@rm -f $(PID_DIR)/*.pid 2>/dev/null || true

	@echo ""
	@echo "=== Clean complete ==="
	@echo "Safe: DB data, source code, .env, node_modules, .venv — untouched"

# ============================================================================
# EXTRA TARGETS
# ============================================================================
status: ## Show status of all services
	@echo "=== AI Holding — Status ==="
	@echo ""
	@printf "  %-14s" "Backend:"; \
	curl -sf http://localhost:8000/health > /dev/null 2>&1 \
		&& echo "✅ Running (port 8000)" \
		|| echo "❌ Down"
	@printf "  %-14s" "Frontend:"; \
	curl -sf http://localhost:3000 > /dev/null 2>&1 \
		&& echo "✅ Running (port 3000)" \
		|| echo "❌ Down"
	@printf "  %-14s" "Nginx:"; \
	curl -sf http://localhost:8080/health > /dev/null 2>&1 \
		&& echo "✅ Running (port 8080)" \
		|| echo "❌ Down"
	@printf "  %-14s" "PostgreSQL:"; \
	(pg_isready -h localhost -p 5432 -q 2>/dev/null || ss -tlnp 2>/dev/null | grep -q ':5432 ') \
		&& echo "✅ Running (port 5432)" \
		|| echo "❌ Down"
	@printf "  %-14s" "Redis:"; \
	(redis-cli ping 2>/dev/null | grep -q PONG || ss -tlnp 2>/dev/null | grep -q ':6379 ') \
		&& echo "✅ Running (port 6379)" \
		|| echo "❌ Down"
	@printf "  %-14s" "Tunnel:"; \
	pgrep -f "cloudflared tunnel" > /dev/null 2>&1 \
		&& echo "✅ Active ($$(cat $(PROJECT_DIR)/.cloudflare_url 2>/dev/null || echo 'URL pending'))" \
		|| echo "❌ Down"
	@printf "  %-14s" "Telegram:"; \
	curl -sf http://localhost:8000/health > /dev/null 2>&1 \
		&& echo "✅ Polling (via backend)" \
		|| echo "❌ Down (backend needed)"
	@echo ""
	@if curl -sf http://localhost:8000/api/dashboard/overview > /dev/null 2>&1; then \
		echo "  Agents:"; \
		curl -sf http://localhost:8000/api/agents 2>/dev/null | \
			python3 -c "import sys,json; [print(f'    ✅ {a[\"name\"]} ({a[\"role\"]}) — {a[\"status\"]}') for a in json.load(sys.stdin)]" 2>/dev/null; \
	fi

build: ## Build frontend for production
	@echo "Building frontend..."
	@cd $(FRONTEND_DIR) && npm run build
	@echo "✓ Frontend built"

db-stop: ## Stop PostgreSQL + Redis containers
	@echo "Stopping database services..."
	@docker compose -f $(PROJECT_DIR)/docker-compose.yml stop postgres redis 2>/dev/null || true
	@echo "  ✓ PostgreSQL + Redis stopped"

db-start: ## Start PostgreSQL + Redis containers
	@echo "Starting database services..."
	@docker compose -f $(PROJECT_DIR)/docker-compose.yml up -d postgres redis
	@echo "  ✓ PostgreSQL + Redis started"

restart: stop start ## Restart everything

logs-backend: ## Tail backend logs
	@tail -f $(LOG_DIR)/backend.log

logs-frontend: ## Tail frontend logs
	@tail -f $(LOG_DIR)/frontend.log

typecheck: ## Run mypy on backend
	@cd $(BACKEND_DIR) && $(VENV)/python -m mypy app/ --ignore-missing-imports
