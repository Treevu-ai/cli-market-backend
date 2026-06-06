# Piloto FMCG — Nuna Orgánica (`nunaorganica_pe`)

**Interno · ops · junio 2026**

## Resumen

| Métrica | Valor |
|---------|-------|
| Store ID | `nunaorganica_pe` |
| Base URL | https://nunaorganica.pe |
| Plataforma | WooCommerce (Store API público) |
| Línea | `supermercados` (FMCG orgánico PE) |
| Catálogo Store API | **406** SKUs |
| Cobertura precios | **99.3%** |
| Cobertura marcas (inferidas) | **92.4%** |
| Linkage índice (muestra 50) | **74%** |
| Queries FMCG con hits | **10/13** |

## Producción (collector)

Verificar en `/dashboard/data` → `store_health` → `nunaorganica_pe`:

- `success_pct`: 100% tras ciclo 2026-06-06 18:42 UTC
- `coverage_7d_pct`: 100%
- Full catalog pull: Store API paginado (sin REST keys) cada ~60 min vía `collect_full_catalog_pg`

## Evaluación local

```bash
python ops/eval_nunaorganica_pilot.py --full-catalog --json
python ops/eval_woocommerce.py --base https://nunaorganica.pe --query leche --json
```

## REST keys (opcional)

Si el retailer aprueba vía `/retailers/apply`:

- `wc_consumer_key` / `wc_consumer_secret` en `store_credentials`
- Habilita REST v3 + categorías; no bloquea el piloto público

## Pagos (paralelo)

Mercado Pago Checkout Pro en sandbox:

- `POST /checkout/mercadopago`
- Webhook: `/checkout/mercadopago-webhook?source_news=webhooks`
- Diagnóstico: `GET /mercadopago-status?test=1`

## Commits de referencia

- core `4964548` — Store API full catalog + brand inference
- backend `71eda07` — `eval_nunaorganica_pilot.py`