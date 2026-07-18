# CLI Market × Niubiz — Propuesta de Partnership

**De:** CLI Market (cli-market.dev)
**Para:** equipo de partnerships / innovación de Niubiz
**Objetivo de esta conversación:** integrar datos de retail normalizados de CLI Market con la red de aceptación de Niubiz para habilitar comercio agéntico en Perú a escala.

---

## En una frase

Niubiz opera la red de aceptación de pagos más grande de Perú — CLI Market ya construyó la capa de datos e infraestructura de control (precios normalizados, auditoría, aprobación humana) que un ecosistema de agentes de esa escala necesita. Proponemos una integración de datos con Niubiz, no un conector que compita con su red.

---

## Por qué Niubiz específicamente

Niubiz conecta a la mayor base de comercios y retailers de Perú a través de su red de aceptación — exactamente el universo de retailers que CLI Market ya normaliza en sus Golden Records. Esa superposición es la base de la propuesta: donde Niubiz ya tiene la relación comercial con el retailer, CLI Market ya tiene el dato de producto/precio normalizado de ese mismo retailer.

El comercio agéntico está emergiendo como categoría (ACP de Stripe, MCP como estándar de integración) y el jugador de mayor escala en Perú tiene la oportunidad de definir cómo se ve esa categoría localmente — no como réplica de un movimiento de otro PSP, sino liderándolo desde la posición de red más grande del país.

**Riel de pago y red de aceptación → los tiene Niubiz. Datos de retailers normalizados de esa misma red → los tiene CLI Market.**

---

## Qué aporta CLI Market (con track record, no en papel)

| Activo | Estado |
|---|---|
| Datos normalizados de retail | Golden Records (`prod_*`) que unifican productos entre múltiples retailers peruanos y de la región, con historial de precios y stock — cobertura que se superpone directamente con la red de comercios de Niubiz. |
| Servidor MCP en producción | 40+ tools ya operando (búsqueda, comparación, checkout, alertas de precio), consumido activamente por agentes hoy. |
| Auditoría de escrituras | Audit log append-only ya en producción — trazabilidad exigible ante cualquier revisión de compliance, un requisito que sabemos es más estricto en el segmento enterprise de Niubiz. |
| Control humano en el loop | Mecanismo de aprobación humana (requester/approver, expiración, umbral por monto) ya implementado y funcionando en un producto real (Procure Copilot) — relevante para el perfil de riesgo más conservador de comercios grandes. |
| Track record de pagos en Perú | Integración real con MercadoPago Perú ya en producción. |
| Modelo de cuentas de negocio | Soporte para organizaciones multi-usuario con roles diferenciados — se alinea con el tipo de cliente corporativo/enterprise que Niubiz ya atiende. |

---

## Qué buscamos de Niubiz

- Una conversación exploratoria de innovación/partnerships para evaluar si el feed de datos normalizados de CLI Market aporta valor a los flujos de cobro agéntico que Niubiz esté evaluando o construyendo.
- Entender el proceso y los requisitos de compliance/seguridad de Niubiz para una integración de datos (dado el perfil enterprise, asumimos un proceso más formal que con un PSP dev-first).
- Explorar si tiene sentido un piloto acotado con un subconjunto de comercios de la red de Niubiz antes de cualquier compromiso de mayor escala.

## Qué NO estamos proponiendo

- No buscamos construir un conector que agregue Niubiz junto a otros PSPs peruanos ni competir con su red de aceptación.
- No buscamos custodiar credenciales ni fondos de Niubiz ni de los comercios de su red.

---

## Siguiente paso

Dado el perfil enterprise de Niubiz, proponemos una llamada exploratoria (30-45 min) con el equipo de innovación/partnerships para entender su proceso de evaluación de integraciones y definir si un piloto acotado tiene sentido antes de escalar.

**Contacto:** Ricardo Cuba Alván — CLI Market (cli-market.dev)
