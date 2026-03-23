#!/usr/bin/env bash
# =============================================================================
# AI Holding — Production Startup Script
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "=== AI Holding Production Startup ==="
echo "Project: $PROJECT_DIR"
echo "Time:    $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# --- 1. Check prerequisites ---
echo "[1/6] Checking prerequisites..."
for cmd in python3 node npm nginx cloudflared; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "  ERROR: $cmd not found. Install it first."
    exit 1
  fi
done

# Check Docker containers (postgres + redis)
if ! docker ps --format '{{.Names}}' | grep -q postgres; then
  echo "  Starting PostgreSQL + Redis containers..."
  cd "$PROJECT_DIR" && docker compose up -d postgres redis
  sleep 3
fi
echo "  ✓ All prerequisites ready"

# --- 2. Build frontend for production ---
echo "[2/6] Building frontend..."
cd "$FRONTEND_DIR"
npm run build > "$LOG_DIR/frontend-build.log" 2>&1
echo "  ✓ Frontend built"

# --- 3. Configure nginx ---
echo "[3/6] Configuring nginx..."
NGINX_CONF="$PROJECT_DIR/nginx/ai-holding.conf"
if [ -f "$NGINX_CONF" ]; then
  sudo ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/ai-holding.conf
  sudo rm -f /etc/nginx/sites-enabled/default
  sudo nginx -t 2>/dev/null
  sudo systemctl restart nginx
  echo "  ✓ Nginx configured on port 8080"
fi

# --- 4. Stop any existing processes ---
echo "[4/6] Cleaning up old processes..."
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "next start" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 2
echo "  ✓ Old processes stopped"

# --- 5. Start backend ---
echo "[5/6] Starting backend..."
cd "$BACKEND_DIR"
source .venv/bin/activate
set -a; source "$PROJECT_DIR/.env" 2>/dev/null; set +a

nohup python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 2 \
  --log-level info \
  > "$LOG_DIR/backend.log" 2>&1 &

BACKEND_PID=$!
echo "  ✓ Backend started (PID: $BACKEND_PID)"

# Wait for backend to be ready
echo "  Waiting for backend..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "  ✓ Backend healthy"
    break
  fi
  sleep 1
done

# --- 6. Start frontend ---
echo "[6/6] Starting frontend..."
cd "$FRONTEND_DIR"
nohup npx next start --port 3000 \
  > "$LOG_DIR/frontend.log" 2>&1 &

FRONTEND_PID=$!
echo "  ✓ Frontend started (PID: $FRONTEND_PID)"

# Wait for frontend
for i in $(seq 1 15); do
  if curl -sf http://localhost:3000 > /dev/null 2>&1; then
    echo "  ✓ Frontend healthy"
    break
  fi
  sleep 1
done

# --- Done ---
echo ""
echo "=== AI Holding is Running ==="
echo "Backend:     http://localhost:8000  (PID: $BACKEND_PID)"
echo "Frontend:    http://localhost:3000  (PID: $FRONTEND_PID)"
echo "Nginx:       http://localhost:8080  (reverse proxy)"
echo "Cloudflare:  Starting via backend... (check /web in Telegram)"
echo ""
echo "Logs: $LOG_DIR/"
echo "Stop: pkill -f 'uvicorn app.main' && pkill -f 'next start'"
echo ""

# Save PIDs for stop script
echo "$BACKEND_PID" > "$LOG_DIR/backend.pid"
echo "$FRONTEND_PID" > "$LOG_DIR/frontend.pid"
