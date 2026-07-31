#!/bin/sh
set -eu
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips="*" \
  --timeout-keep-alive 30 \
  --timeout-graceful-shutdown 30
