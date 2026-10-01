#!/usr/bin/env bash
set -eu

: "${RAILWAY_API_ORIGIN:?Set RAILWAY_API_ORIGIN in Netlify to your public Railway service origin (for example, https://example.up.railway.app)}"
case "$RAILWAY_API_ORIGIN" in
  https://*) ;;
  *) echo "RAILWAY_API_ORIGIN must start with https://" >&2; exit 1 ;;
esac

api_origin="${RAILWAY_API_ORIGIN%/}"
printf '/api/* %s/api/:splat 200\n/* /index.html 200\n' "$api_origin" > frontend/_redirects
echo "Configured Netlify API proxy to ${api_origin}"
