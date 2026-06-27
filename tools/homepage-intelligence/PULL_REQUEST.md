# Homepage: Live Agentic Commerce Pulse embed

## Summary

Embeds the **Intelligence Terminal** on the cli-market.dev homepage (`#intelligence` section):

- New `CommercePulseEmbed` React component (iframe → `GET /embed/commerce-pulse`)
- Updated `IntelligenceSection` with live pulse widget + fourth bullet
- Links to full report at `/intelligence` on the API host

Depends on backend PR: [Treevu-ai/cli-market-backend#87](https://github.com/Treevu-ai/cli-market-backend/pull/87) (or equivalent deploy with `/embed/commerce-pulse`).

## Apply options

### Option A — `git apply` (recommended)

From **cli-market-world** repo root:

```bash
curl -sL https://raw.githubusercontent.com/Treevu-ai/cli-market-backend/cursor/market-brief-intelligence-terminal-81ed/tools/homepage-intelligence/homepage-intelligence.patch \
  | git apply --check
curl -sL https://raw.githubusercontent.com/Treevu-ai/cli-market-backend/cursor/market-brief-intelligence-terminal-81ed/tools/homepage-intelligence/homepage-intelligence.patch \
  | git apply
```

Or copy `tools/homepage-intelligence/` from the backend branch and run:

```bash
./tools/homepage-intelligence/apply.sh
```

### Option B — Manual copy

| Source (backend branch) | Destination (cli-market-world) |
|-------------------------|----------------------------------|
| `tools/homepage-intelligence/CommercePulseEmbed.tsx` | `landing/components/CommercePulseEmbed.tsx` |
| `tools/homepage-intelligence/IntelligenceSection.tsx` | `landing/components/IntelligenceSection.tsx` |

`landing/app/page.tsx` already imports `<IntelligenceSection />` — no route changes needed.

## Environment

```env
# landing/.env.local (or Vercel project env)
NEXT_PUBLIC_API_URL=https://cli-market-production.up.railway.app
```

## Backend CSP (iframe)

The API must allow framing from cli-market.dev. Default in `intelligence_web.py`:

```
INTEL_EMBED_FRAME_ANCESTORS='self' https://cli-market.dev https://www.cli-market.dev http://localhost:3000
```

Set on Railway/Render if the homepage domain differs.

## Verify locally

```bash
cd landing
npm run dev
# Open http://localhost:3000/#intelligence — iframe should show Commerce Pulse for PE
```

## Verify production

1. Deploy backend with intelligence routes live
2. Merge this PR and deploy landing
3. Open https://cli-market.dev/#intelligence
4. Confirm iframe loads; "Reporte completo →" opens API `/intelligence`

## CLI / MCP map (for docs)

| Surface | Endpoint / command |
|---------|-------------------|
| Homepage iframe | `GET /embed/commerce-pulse?country=PE` |
| Full report | `GET /intelligence?country=PE` |
| JSON widget | `GET /public/intelligence/data` |
| CLI | `market pulse`, `market forecast`, `market arbitrage` |
| MCP | `market_intel_pulse`, `market_forecast`, `market_arbitrage` |

## Checklist

- [ ] Backend PR #87 merged / deployed
- [ ] `NEXT_PUBLIC_API_URL` set in Vercel
- [ ] `INTEL_EMBED_FRAME_ANCESTORS` includes production homepage origin
- [ ] `npm run build` passes in `landing/`
- [ ] Manual check on `#intelligence` (ES + EN via language toggle)
