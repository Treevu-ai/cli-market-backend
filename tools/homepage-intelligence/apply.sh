#!/usr/bin/env bash
# Apply Intelligence Terminal homepage embed to cli-market-world.
# Run from the root of a cli-market-world clone.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PATCH="${ROOT}/tools/homepage-intelligence/homepage-intelligence.patch"

if [[ ! -f landing/components/IntelligenceSection.tsx ]]; then
  echo "error: run from cli-market-world repo root (landing/ not found)" >&2
  exit 1
fi

if [[ ! -f "$PATCH" ]]; then
  echo "error: patch not found at $PATCH" >&2
  exit 1
fi

echo "Applying homepage intelligence patch..."
git apply --check "$PATCH"
git apply "$PATCH"

echo ""
echo "Done. Next steps:"
echo "  1. Ensure NEXT_PUBLIC_API_URL is set in landing/.env.local (or Vercel):"
echo "     NEXT_PUBLIC_API_URL=https://cli-market-production.up.railway.app"
echo "  2. cd landing && npm run build"
echo "  3. Verify iframe loads at https://cli-market.dev/#intelligence"
