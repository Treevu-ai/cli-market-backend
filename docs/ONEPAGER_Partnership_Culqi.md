# CLI Market × Culqi — Propuesta de Partnership

**De:** CLI Market (cli-market.dev)
**Para:** equipo de partnerships / developer relations de Culqi
**Objetivo de esta conversación:** integrar datos de retail normalizados de CLI Market con el riel de pago de Culqi para habilitar comercio agéntico en Perú.

---

## En una frase

Culqi ya es el PSP más dev-first de Perú — CLI Market ya construyó la capa de datos e infraestructura de control (precios normalizados, auditoría, aprobación humana) que un agente necesita antes de pagar. Proponemos integrarnos con el riel de Culqi vía API, no construir un conector que compita con él.

---

## Por qué Culqi específicamente

Culqi es el PSP peruano con la propuesta de valor más orientada a developers y startups — API-first, documentación pública, SDKs modernos. CLI Market opera de la misma forma: un servidor MCP consumido por agentes de IA hoy, no un producto de checkout tradicional. El fit técnico y de audiencia (developers construyendo agentes/SaaS) es el más directo de los tres PSPs peruanos.

El comercio agéntico está tomando forma con protocolos como ACP (Stripe) y MCP como estándar de integración de agentes. Ser el primer PSP peruano con una integración de datos + agente demostrable es una ventana de diferenciación concreta frente a Niubiz y BCP.

**Riel de pago → lo tiene Culqi. Datos de retailers normalizados → los tiene CLI Market.**

---

## Qué aporta CLI Market (con track record, no en papel)

| Activo | Estado |
|---|---|
| Datos normalizados de retail | Golden Records (`prod_*`) que unifican productos entre múltiples retailers peruanos y de la región, con historial de precios y stock. |
| Servidor MCP en producción | 40+ tools ya operando (búsqueda, comparación, checkout, alertas de precio), consumido activamente por agentes hoy. |
| Auditoría de escrituras | Audit log append-only ya en producción — cada operación de escritura queda trazada. |
| Control humano en el loop | Mecanismo de aprobación humana (requester/approver, expiración, umbral por monto) ya implementado y funcionando en un producto real (Procure Copilot). |
| Track record de pagos en Perú | Integración real con MercadoPago Perú ya en producción — no es un actor nuevo sin experiencia moviendo cobros. |
| Modelo de cuentas de negocio | Soporte para organizaciones multi-usuario, relevante para el segmento startup/PyME que Culqi ya atiende. |

---

## Qué buscamos de Culqi

- Acceso a la API de Culqi como método de cobro dentro del ecosistema de agentes de CLI Market — el camino más rápido de los tres porque Culqi ya expone API pública y sandbox self-serve.
- Una integración técnica ligera primero (sandbox, sin conversación comercial extensa) para tener una demo funcionando antes de escalar a partnership formal.
- Explorar si Culqi quiere consumir el feed de datos de retail de CLI Market para sus propios flujos agénticos, más allá de ser solo el riel de cobro.

## Qué NO estamos proponiendo

- No buscamos construir un conector que agregue Culqi junto a Niubiz/BCP/otros — cada integración es independiente.
- No buscamos custodiar credenciales ni fondos de Culqi ni de sus comercios.

---

## Siguiente paso

Dado el perfil dev-first de Culqi, proponemos empezar directo en sandbox: una llamada corta (30 min) con developer relations para validar el modelo de integración, seguida de una implementación técnica de referencia antes de escalar a conversación de partnership comercial.

**Contacto:** Ricardo Cuba Alván — CLI Market (cli-market.dev)
