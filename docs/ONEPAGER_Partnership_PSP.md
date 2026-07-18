# CLI Market × [Culqi / Niubiz / BCP] — Propuesta de Partnership

**De:** CLI Market (cli-market.dev)
**Para:** equipo de partnerships / producto de [PSP]
**Objetivo de esta conversación:** explorar una integración de datos entre CLI Market y [PSP] para comercio agéntico en Perú.

---

## En una frase

CLI Market ya construyó la capa de datos e infraestructura de control (precios normalizados de retail, auditoría, aprobación humana) que un PSP necesita para habilitar cobro agéntico en Perú — buscamos integrarnos con [PSP], no competir con su riel de pago.

---

## El problema que ambos resolvemos mejor juntos

El comercio agéntico (agentes de IA que compran y pagan en nombre de un usuario o negocio) está creciendo globalmente — Stripe ya tiene su propio protocolo de comercio agéntico (ACP), y hay tracción real detrás de MCP (Model Context Protocol) como estándar de integración para agentes.

En Perú, ningún PSP tiene hoy una capa de datos de retail normalizada para dar contexto a un agente antes de que pague (qué está comprando, a qué precio real, si el precio es confiable, si hay stock). Y ningún actor de datos de retail en Perú tiene un riel de pago propio — ni debería construirlo: ese es el negocio de [PSP], no el nuestro.

**Riel de pago → lo tiene [PSP]. Datos de retailers normalizados → los tiene CLI Market.**

---

## Qué aporta CLI Market (con track record, no en papel)

| Activo | Estado |
|---|---|
| Datos normalizados de retail | Golden Records (`prod_*`) que unifican productos entre múltiples retailers peruanos y de la región, con historial de precios y stock. |
| Servidor MCP en producción | 40+ tools ya operando (búsqueda, comparación, checkout, alertas de precio), consumido activamente por agentes hoy. |
| Auditoría de escrituras | Audit log append-only ya en producción — cada operación de escritura queda trazada. |
| Control humano en el loop | Mecanismo de aprobación humana (requester/approver, expiración, umbral por monto) ya implementado y funcionando en un producto real (Procure Copilot). |
| Track record de pagos en Perú | Integración real con MercadoPago Perú ya en producción — CLI Market no es un actor nuevo sin experiencia moviendo cobros en el mercado peruano. |
| Modelo de cuentas de negocio | Soporte para organizaciones multi-usuario (no solo compradores individuales) — relevante si [PSP] quiere habilitar agentes del lado comercio, no solo consumidor. |

---

## Qué buscamos de [PSP]

- Acceso al riel de pago de [PSP] como opción de cobro dentro del ecosistema de agentes de CLI Market.
- Explorar qué forma toma la integración de datos: ¿[PSP] consume el feed de precios/Golden Records de CLI Market para dar contexto a sus propios flujos de cobro agéntico? ¿O CLI Market referencia el riel de [PSP] como método de pago dentro de sus propios tools MCP?
- Una conversación técnica corta (30-45 min) para definir el modelo de integración concreto antes de comprometer ingeniería de cualquiera de los dos lados.

## Qué NO estamos proponiendo

- No buscamos construir un conector propio que agregue o compita con [PSP] junto a otros PSPs peruanos.
- No buscamos custodiar credenciales ni fondos de [PSP] ni de sus comercios — el riel de pago sigue siendo 100% de [PSP].

---

## Siguiente paso

Una llamada exploratoria con el equipo de partnerships/producto de [PSP] para validar interés y definir el modelo de integración de datos. Podemos traer una demo en vivo del servidor MCP de CLI Market y del flujo de aprobación humana ya en producción.

**Contacto:** Ricardo Cuba Alván — CLI Market (cli-market.dev)
