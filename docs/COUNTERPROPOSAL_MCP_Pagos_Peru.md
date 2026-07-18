# Contrapropuesta — Conector MCP de Cobro Perú (vs. PRD "Multi-PSP Culqi+Niubiz")

**Documento base:** `PRD_MCP_Pagos_Peru.md` (Draft v1, Julio 2026, Owner: Ricardo Cuba Alván / Sinapsis Innovadora S.A.C.)
**Método de validación:** revisión del PRD contra el multirepo real (cli-market-backend, cli-market-core, cli-market-world, cli-market-index, procure-copilot) en tres rondas de verificación.
**Estado:** Draft v1 — para discusión antes de cualquier `/plan` de implementación.

---

## Resumen ejecutivo

El PRD original trata el conector de cobro peruano como territorio virgen y propone construirlo desde cero, con owner Sinapsis Innovadora, dueño de credenciales de terceros, en 4-6 semanas para Culqi+Niubiz simultáneos.

La revisión del multirepo confirma **una brecha real** — nadie en el ecosistema puede hoy cobrarle a los clientes de un tercero vía Culqi/Niubiz — pero también muestra que:

1. Gran parte de la plumbing que el PRD pide diseñar **ya existe y es reusable** (orders, idempotencia, webhooks, audit log, cola de aprobación humana, modelo de organización multi-usuario).
2. La pieza que falta de verdad — **custodia de credenciales de comercio para cobro entrante** — no tiene ningún precedente en el ecosistema, y es la de mayor riesgo legal/técnico.
3. El propio material de posicionamiento de CLI Market ya se define como partner de datos *hacia* los PSPs (Culqi/BCP/Niubiz), no como constructor de un conector que compite/agrega sobre ellos — y esa es la vía que se adopta en esta contrapropuesta (ver "Decisión estratégica" más abajo).

**Recomendación: no ejecutar el PRD tal como está.** No se construye ningún conector de cobro propio. Se redefine el objetivo hacia partnership directo con los PSPs, usando la infraestructura y el track record que CLI Market ya tiene como el pitch técnico de esa conversación.

---

## Qué existe ya en el multirepo (evidencia)

| Requisito del PRD | Estado real | Dónde |
|---|---|---|
| Esquema unificado de tools multi-PSP | **No existe, y es exclusión deliberada.** `market_connectors/base.py` (`BaseConnector`) es la única abstracción unificada del repo, y `CONNECTOR_PATTERN.md` la scopea explícitamente a conectores de *producto* (VTEX, Shopify, WooCommerce, Magento, UCP) — pagos quedan fuera a propósito. Los 4 gateways de pago actuales (PayPal, MercadoPago, Wise, Lemon) son módulos sueltos con shapes de respuesta distintas entre sí (`approve_url` vs `checkout_url` vs `qr_url`). | `cli-market-core/CONNECTOR_PATTERN.md`, `market_connectors/*.py` |
| Tools de lectura (saldo, transacciones, estado) | Patrón resuelto y reusable. | `db_find_order_by_id`, `db_get_cart`, `check_budget` |
| Auditoría inmutable de escrituras | Ya existe: `audit_log` append-only + `record_audit()` genérico, usado en admin/billing. | `cli-market-world/market_audit.py` |
| Idempotencia | Ya existe y bien pensada: header `Idempotency-Key`, replay detection sin doble-contar budget. | `routers/payments.py::_prepare_pending_order` |
| Modo sandbox | Patrón trivial de replicar (`PAYPAL_SANDBOX` env var). | `market_connectors/paypal_payments.py` |
| **Aprobación humana obligatoria por transacción** | **Ya existe, no es net-new.** `lib/approval.ts`: `createApprovalRequest` / `respondToApproval` / `getPendingApprovals`, roles requester/approver separados, expiración, persistido en D1. Flujo: `POST /api/procurement/run → approve → checkout`, gateado por tier y umbral de monto. | `procure-copilot/lib/approval.ts` |
| Modelo de organización / cuenta de negocio | **Ya existe, no es net-new.** Presupuestos scoped a `organizationId`, roles distintos por org. | `procure-copilot/lib/budget.ts`, `lib/types.ts` |
| Vault de método de pago (prevención IDOR) | Existe, pero para *comprador* (tarjeta guardada propia), no para credenciales de comercio. | `cli-market-world/market_vault.py` (`vault_bindings`) |
| Rieles de cobro Perú ya en producción | MercadoPago Perú real (`create_preference`), usado en `/billing/pro-checkout` y `/billing/procure-subscribe`. Yape/Plin es QR estático manual, no integración PSP. | `market_connectors/mercadopago_payments.py` |
| **Custodia de credenciales de comercio para cobro entrante** | **Cero precedente en los 5 repos.** Todo flujo de pago encontrado es *outbound* (el usuario/org paga por algo), nunca *inbound* (un negocio cobrando a sus propios clientes). Ningún modelo multi-tenant de secretos de PSP existe. | — (ausencia confirmada) |
| Culqi/Niubiz como algo a construir | **Conflicto de posicionamiento.** El material GTM de CLI Market los trata como *partners potenciales de datos*, no como PSPs a agregar/competir. | `cli-market-world/tools/content-repo-template/strategy/api-positioning-es.md:64` |

---

## La brecha real (lo único genuinamente nuevo)

Después de tres rondas de verificación, lo que el PRD pide y **no tiene ningún precedente en el ecosistema** se reduce a una sola cosa:

> **Un negocio (PyME) cobrando dinero a sus propios clientes finales vía Culqi/Niubiz, con CLI Market/Sinapsis custodiando o intermediando esas credenciales.**

Todo lo demás (aprobación humana, idempotencia, audit log, modelo de organización, sandbox) tiene un patrón ya construido en algún punto del multirepo. Esta es la única pieza que exige diseño desde cero, y es también la de mayor exposición legal (la pregunta bloqueante de SBS del propio PRD aplica exactamente aquí, no al resto).

---

## Decisión estratégica: partnership, no construcción propia

Se evaluaron tres direcciones para resolver la tensión detectada (Sinapsis como dueño de un agregador multi-PSP vs. el posicionamiento ya existente de CLI Market como partner de datos):

1. Construir conectores propios Culqi+Niubiz (propuesta original del PRD).
2. Integrarse como cliente de un agregador de pagos multi-PSP ya existente (Kushki, dLocal, PayU, etc.) y/o posicionarse dentro de la capa emergente de comercio agéntico (MCP registries, ACP-style protocols).
3. **Partnership directo con los PSPs** (Culqi, Niubiz, BCP), aportando la capa de datos/agéntica de CLI Market a cambio de acceso al riel de pago — sin construir ni operar infraestructura de cobro propia.

**Decisión: opción 3.** Es la más viable y la de menor riesgo, y además es la única de las tres que ya tiene precedente real en el propio material de GTM: `cli-market-world/tools/content-repo-template/strategy/api-positioning-es.md:59-68` ya argumenta exactamente esto — "el PSP que lo adopte primero necesitará: 1. Riel de pago — lo tiene el PSP, 2. Datos de retailers normalizados — los tiene CLI Market". No hay que inventar una tesis nueva, hay que ejecutar la que ya está escrita.

Se descarta la opción 2 (agregador/capa de agregación agéntica): confirmado por búsqueda en los 5 repos que no existe ningún precedente de código o estrategia apuntando a un agregador específico — habría que evaluar vendors desde cero, sin ninguna base existente, y seguiría exponiendo a CLI Market como intermediario técnico de flujos de pago (mismo riesgo legal/SBS del PRD original, solo que delegado a un tercero en vez de construido in-house).

Se descarta la opción 1 (PRD original) por las razones ya documentadas: cero precedente de custodia de credenciales de comercio, mayor exposición legal, y choque directo con el posicionamiento GTM existente.

**Qué cambia esto en la práctica:** el esfuerzo deja de ser mayormente ingeniería (construir conectores, aprobación humana, vault) y pasa a ser mayormente biz-dev — conseguir la conversación con Culqi/Niubiz/BCP. La ingeniería que sí aplica es empaquetar lo que **ya existe** en el multirepo como el pitch técnico de esa conversación, no construir nada nuevo de cobro.

---

## Contrapropuesta

### 1. Reencuadra el objetivo: de "construir un conector de cobro" a "ser la capa de datos que un PSP quiere integrar"

No hay checkout, PSP connector, ni vault que construir en v1. Lo que hay que preparar es la evidencia de que CLI Market ya es la contraparte de datos que un PSP necesitaría, usando piezas que **ya existen y son reusables** como prueba de madurez técnica en la conversación de partnership:

- `market_audit.record_audit()` (`cli-market-world/market_audit.py`) — audit log append-only ya operando en producción, no una promesa.
- `procure-copilot/lib/approval.ts` — cola de aprobación humana (requester/approver, expiración, D1) ya funcionando en un producto real, evidencia de que CLI Market entiende el requisito de control humano que cualquier PSP va a exigir.
- `organizationId`-scoped budgets (`procure-copilot/lib/budget.ts`) — modelo de cuenta de negocio multi-usuario ya construido.
- El order/idempotency plumbing de `routers/payments.py` — evidencia de manejo correcto de checkout/webhooks con 4 PSPs ya en producción (PayPal, MercadoPago, Wise, Lemon).
- `market_connectors/mercadopago_payments.py` — MercadoPago Perú real, prueba de que CLI Market ya opera pagos en el mercado peruano, no es un actor nuevo sin trackrecord.

### 2. La única pieza de ingeniería nueva: preparar el "data product" que se ofrece al PSP

Si la propuesta de valor es "datos de retailers normalizados" (según el propio doc de posicionamiento), el trabajo real es empaquetar eso como API/feed consumible por un PSP — no un conector de cobro. Esto es una extensión natural de lo que ya existe (`index_gate.py`, Golden Records `prod_*`, `market_scores`, etc.), no un producto nuevo.

### 3. No comprometas alcance de "Culqi+Niubiz simultáneo" en v1 de la conversación

La primera conversación de partnership no necesita ambos PSPs a la vez. Prioriza uno (evaluar cuál tiene mejor fit de API/dev experience o mayor apertura a partnerships de datos — esto es investigación externa, no algo que el código resuelva) y usa esa relación como caso de referencia para la segunda.

### 4. Repo/producto: esto vive en `cli-market-world`, no en un producto aparte de Sinapsis

Al ser partnership de datos y no un producto de cobro nuevo, no hay razón para separarlo de CLI Market — al contrario, el valor de la propuesta depende de que sea *el mismo* CLI Market con track record, no un producto nuevo sin usuarios. Esto también resuelve la pregunta de reuso: todo lo listado en la tabla de evidencia aplica directamente, sin fricción de stack (ya vive en `cli-market-world`/`cli-market-core`).

---

## Preguntas bloqueantes actualizadas (reemplaza la sección "Open Questions" del PRD)

1. **[Producto — nuevo, bloqueante]** ¿Cuál de los tres PSPs (Culqi, Niubiz, BCP) es el objetivo de la primera conversación de partnership? Requiere investigación externa (apertura a partnerships, madurez de API/dev experience, señales de interés en comercio agéntico) — no se resuelve desde el código.
2. **[Producto — nuevo, bloqueante]** ¿Cuál es exactamente el "data product" a ofrecer? (feed de precios normalizados, Golden Records, scores de mercado, algo más) — define el alcance real de la única pieza de ingeniería nueva.
3. **[Legal — recortado, ya no es tan crítico]** La pregunta de SBS del PRD original pierde peso en este escenario: CLI Market deja de tocar flujos de pago directamente. Vale confirmar igual, pero ya no es la bloqueante principal.
4. **[Stakeholder — igual que el PRD]** Monetización del partnership (revenue share por datos, fee fijo, u otro modelo) — sigue sin resolverse.

---

*Nota: este documento no reemplaza al PRD original — lo complementa con lo que la revisión del código confirmó, corrigió o refutó tras tres rondas de verificación contra cli-market-backend, cli-market-core, cli-market-world, cli-market-index y procure-copilot.*
