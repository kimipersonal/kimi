#!/usr/bin/env bash
# Stop all AI Holding services (separate script so pkill doesn't kill make)
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Backend — kill by PID file, then sweep
if [ -f "$DIR/logs/backend.pid" ]; then
    kill "$(cat "$DIR/logs/backend.pid")" 2>/dev/null || true
    rm -f "$DIR/logs/backend.pid"
fi
# Sweep any remaining uvicorn workers
pids=$(ps aux | grep '[u]vicorn.*app\.main' | awk '{print $2}')
for p in $pids; do kill "$p" 2>/dev/null; done
echo "  ✓ Backend stopped"

# Frontend — kill by PID file, then sweep
if [ -f "$DIR/logs/frontend.pid" ]; then
    kill "$(cat "$DIR/logs/frontend.pid")" 2>/dev/null || true
    rm -f "$DIR/logs/frontend.pid"
fi
pids=$(ps aux | grep '[n]ext-server\|[n]ext.*start' | awk '{print $2}')
for p in $pids; do kill "$p" 2>/dev/null; done
echo "  ✓ Frontend stopped"

# Cloudflare tunnel
pids=$(ps aux | grep '[c]loudflared.*tunnel' | awk '{print $2}')
for p in $pids; do kill "$p" 2>/dev/null; done
rm -f "$DIR/.cloudflare_url"
echo "  ✓ Tunnel stopped"

# Nginx
sudo systemctl stop nginx 2>/dev/null || true
echo "  ✓ Nginx stopped"

sleep 1
