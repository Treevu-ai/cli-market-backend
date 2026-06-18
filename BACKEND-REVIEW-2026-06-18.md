# Backend parity review — 2026-06-18

Revisión post-cambios en world. El backend (cli-market-backend) está limpio en
lo crítico, pero hay discrepancias menores que conviene revisar.

---

## 1. mcp.json — métricas diferentes a world

| Campo | Backend | World |
|-------|---------|-------|
| Tools | 43 | 22 curated / 46 legacy |
| Retailers | 36 en 11 países | 38 en 8 países |
| Precios | 45,000+ | 60,000+ |
| Indicadores | 34 | 63 |

**¿Es intencional?** El backend expone 43 tools (perfil completo) vs las 22 del
perfil default de world. Puede ser correcto si el backend tiene un scope
distinto. Pero si es mirror de prod, debería reflejar las métricas de world.

**Acción**: definir si backend debe copiar los stats de world o mantener los
propios. Si es mirror, actualizar los números en `mcp.json`.

```bash
cd cli-market-backend
# Editar mcp.json y cambiar:
#   45,000+ → 60,000+  (si aplica)
#   34 market indicators → 63 market indicators  (si aplica)
```

---

## 2. requirements.txt — pin suelto de cli-market-core

```
# Backend
cli-market-core==1.9.42

# World
cli-market-core==1.9.42
```

World pinea versión exacta. Backend usa `>=`. Esto puede causar drift si se
publica una versión de core que rompa algo.

**Acción**: pinear a la misma versión que world para paridad:

```bash
cd cli-market-backend
# En requirements.txt, cambiar:
#   cli-market-core==1.9.42
# por:
#   cli-market-core==1.9.42
```

---

## 3. Dockerfile — CACHE_BUST antiguo

```
ARG CACHE_BUST=2026-06-11-release-1.9.30
```

Una semana de antigüedad. Si hubo cambios en `requirements.txt` o
`requirements-private.txt` que no se reflejan en el build cache, conviene
actualizarlo.

**Acción**: bump a fecha de hoy:

```bash
cd cli-market-backend
# En Dockerfile, cambiar:
#   ARG CACHE_BUST=2026-06-11-release-1.9.30
# por:
#   ARG CACHE_BUST=2026-06-18
```

---

## 4. Sin riesgo — confirmado

- **market_cli_i18n**: ni backend ni core lo importan. Sin riesgo de
  ModuleNotFoundError.
- **Dockerfile**: ya usa `python:3.12-slim` (sin el problema del MCR que tuvo
  world). No necesita cambios de base image.
- **Playwright**: `playwright>=1.45.0` está en requirements.txt. Si se necesita
  Chromium en el backend, agregar al Dockerfile después del pip install:
  ```dockerfile
  RUN python -m playwright install chromium --with-deps
  ```

---

## Orden sugerido

1. Pinear `cli-market-core==1.9.42` en `requirements.txt`
2. Bump `CACHE_BUST` en `Dockerfile`
3. Revisar stats de `mcp.json` (solo si backend debe reflejar world)
4. Commit + push a `main`
