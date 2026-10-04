#!/usr/bin/env sh
cd "$(dirname "$0")" && STOCK_DASHBOARD_HOST=0.0.0.0 exec python3 app.py "$@"
