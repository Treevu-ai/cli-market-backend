---
title: LinkedIn Data Gate — Semana 2
tags:
  - linkedin
  - metrics
  - gate
hub: "[[GTM-Hub]]"
date: 2026-05-30
status: active
---

# LinkedIn Data Gate — Antes de publicar días 8–14

**Regla:** No publicar posts de "data stories" sin cifras verificables en esta nota o en [[metrics/price-pulse-YYYY-WW]].

## Fuente de verdad

```bash
curl -sS https://cli-market-production.up.railway.app/dashboard/data | python3 -m json.tool
curl -sS https://cli-market-production.up.railway.app/health/db | python3 -m json.tool
```

Export semanal: `python3 ops/sync_linkedin_metrics.py` → `metrics/price-pulse-YYYY-WW.md`

## Snapshot verificado — 2026-05-30 ✅ GATE PASSED

| Métrica | Valor | OK para LI? |
|---------|-------|-------------|
| Precios indexados (moat) | **43,415** | ✅ |
| Refresh 24h | **37,731** | ✅ |
| Tiendas fresh 24h | **35** | ✅ |
| Tiendas con datos | **35** | ✅ |
| **coverage_7d_pct** | **97.2%** | ✅ gate semana 2 |
| Collector last run | collector activo · 35 tiendas indexadas | ✅ |
| `price_snapshots_upsert_ready` | **true** | ✅ |
| Store success % (lifetime) | 55.6% | ❌ solo ops |
| Moat stale | **false** | ✅ |

### Por país (indexado)

| País | Precios | Tiendas |
|------|---------|---------|
| AR | 6,106 | 8 |
| BR | 4,979 | 11 |
| MX | 3,927 | 4 |
| PE | 3,157 | 4 |
| CO | 1,103 | 3 |

## Gate checklist (semana 2)

- [x] **Moat coverage 7d** ≥ **80%** — **97.2%**
- [x] Upsert Postgres operativo (`price_snapshots_upsert_ready: true`)
- [x] Refresh 24h > 0
- [ ] Cifra `[N]` en Day 07 → usar **37,731** (24h) o **43,415** (indexado)
- [x] Claims agregados 37K+ / 43K+ — OK
- [ ] Claims de inflación % por producto — posponer (sin serie 7–14d aún)
- [ ] No implicar INEI/INDEC — usar "según nuestro collector"

## Posts — estado de publicación

| Día | Tema | Estado | Cifras a usar |
|-----|------|--------|---------------|
| 7 | Semana 1 wrap | ✅ publicar | 37,731 fresh · 43,415 indexados · 35 fresh |
| 8 | Arroz PE variación | ✅ publicar | S/ 2.90–4.40+ · ver [[metrics/query-arroz-pe.json]] |
| 9 | Canasta PE | ✅ cualitativo | multi-tienda · evitar S/147–182 |
| 10 | Collector 8h | ✅ publicar | 37,731 fresh · 43,415 indexados |
| 11 | Electro/hogar | ✅ publicar | 591 electro · 11 países/líneas |
| 12 | Top 10 carousel | ⚠️ | armar desde dashboard top_discounts |
| 13 | Retailers EN | ✅ | sin cifras duras |
| 14 | 3 insights | ✅ publicar | 43,415 moat · 37,731 fresh |


## Query reproducible

```bash
# Day 8 — guardado en repo
curl -sS -X POST https://cli-market-production.up.railway.app/products/compare \
  -H 'Content-Type: application/json' \
  -d '{"query":"arroz","country":"PE","limit":20}' \
  > docs/metrics/query-arroz-pe.json
```

## Decisión 2026-05-29

**Semana 2 desbloqueada.** Publicar días 7–14 con cifras agregadas verificadas. Day 8: variación arroz PE con rango real, sin % inflación inventado. Day 9: cualitativo + demo basket, sin totales ficticios.

## Gate AR — canasta (bloque cero)

**Post LinkedIn AR:** [[linkedin/Day-09-AR]] · **Copy API:** [[api-positioning-es]]

**Regla:** No publicar brecha `[X]`% hasta que las tres cadenas del trío ARS sean comparables.

### Snapshot prod — 2026-05-29 (post PR #16–#18, pre/post-deploy según Railway)

| Tienda | Ítems canasta | Total ARS | ¿Publicable? |
|--------|---------------|-----------|--------------|
| Carrefour AR | **6/10** | 37,854 | ⚠️ parcial |
| Jumbo AR | **3/10** | 3,471 | ❌ bajo umbral 6/10 |
| Vea AR | *(ausente en canasta_basica)* | — | ❌ <3 ítems o sin match |

**Gate AR:** ❌ **BLOQUEADO** — falta completitud ≥6/10 en las 3 cadenas y total comparable.

### marketing_spreads ARS (referencia — por ítem, no canasta total)

| Ítem | spread_ratio | stores | Nota |
|------|--------------|--------|------|
| queso | 2.93x | 3 | OK umbral 2.5x — titular **por ítem**, no % canasta |
| pan | 2.75x | 3 | idem |
| aceite | 2.65x | 3 | idem — post parser cc/1.5L |

**No confundir** `spread_ratio` (brecha min–max / promedio dentro del ítem) con `% más cara vs más barata` entre cadenas en canasta total.

### Comando de verificación

```bash
python3 ops/check_ar_canasta_gate.py
python3 ops/check_ar_canasta_gate.py --watch --interval 300   # poll cada 5 min
```

[[GTM-Hub]] · [[metrics/price-pulse-2026-W22]] · [[linkedin/00-Index]]
