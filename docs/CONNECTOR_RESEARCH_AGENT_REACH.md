# Investigación previa a un conector nuevo — con Agent Reach

Antes de escribir un conector para una tienda nueva (como en la expansión de HotSale.pe, o Licorerías Unidas + Funcar), hay una fase de reconocimiento manual que hoy se hace a mano: identificar la plataforma del sitio, confirmar los endpoints reales, y ver si ya existe un conector open-source parecido. [Agent Reach](https://github.com/Panniantong/Agent-Reach) (clonado en `~/Proyectos/Agent-Reach`) puede acelerar esa fase.

**Alcance de este documento: solo investigación pre-conector.** No toca el pipeline de precios, no genera ningún número que termine en un indicador — es research manual, igual que abrir el sitio en el navegador, pero con el agente haciéndolo.

## Cuándo usarlo

Cuando vayas a agregar una tienda nueva y todavía no sabes:
- Qué plataforma de e-commerce usa (VTEX, Magento, Shopify, WooCommerce, u otra).
- Si ya existe un conector open-source para esa plataforma que se pueda adaptar.
- Cómo está estructurado su catálogo (categorías, si expone API pública, si bloquea bots).

## Checklist

### 1. Identificar la plataforma
Pide al agente: "lee `https://www.tiendaejemplo.com` y dime qué plataforma de e-commerce usa" (Agent Reach usa Jina Reader — trae texto limpio, no HTML crudo). Señales típicas:
- VTEX: rutas `/api/catalog_system/pub/...` o `/io/api/...` responden JSON (igual patrón que usa `ops/verify_new_stores.py` para probar `VTEX_SEARCH_PATHS`).
- Magento: paths `/rest/V1/...` o HTML con `Mage.Cookies`/`data-mage-init` en el código fuente.
- Shopify: `/products.json` público casi siempre disponible.
- WooCommerce: `/wp-json/wc/store/v1/products` o marcas de WordPress/WooCommerce en el HTML.

### 2. Confirmar el endpoint real
No asumir por la plataforma detectada — pedirle al agente que intente leer el endpoint candidato (ej. `https://www.tiendaejemplo.com/api/catalog_system/pub/products/search?ft=leche` para VTEX) y confirmar que responde productos, no un 403/404. Esto es el mismo chequeo manual que hace `ops/verify_new_stores.py`, solo que antes de escribir el registro de la tienda en vez de después.

### 3. Buscar un conector existente
Pide al agente que busque en GitHub (`gh search code` / lector de GitHub de Agent Reach) conectores públicos para esa plataforma+país que se puedan usar de referencia — patrones de paginación, headers anti-bot, manejo de `list_price` vs `price`.

### 4. Registrar hallazgos, no decisiones automáticas
El resultado de este paso es una nota (plataforma detectada, endpoint confirmado, referencia de conector si existe) que un humano usa para escribir el conector siguiendo el patrón que ya usa el repo — Agent Reach no escribe el conector ni decide `platform` por su cuenta.

## Qué NO hacer con Agent Reach aquí

- No usarlo para traer precios "de muestra" que terminen en un reporte o indicador — los precios solo entran al sistema por el colector real, nunca por una lectura ad hoc del agente.
- No usar los canales que requieren login (Twitter, Reddit, Instagram, etc.) para esta fase — no aportan nada a investigar una plataforma de e-commerce.
- No dejar que sustituya la verificación real del colector (`ops/verify_new_stores.py` o el equivalente vigente) antes de habilitar la tienda en producción.
