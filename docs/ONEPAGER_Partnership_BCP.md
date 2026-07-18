# CLI Market × BCP — Propuesta de Partnership

**De:** CLI Market (cli-market.dev)
**Para:** equipo de innovación / Yape / partnerships digitales de BCP
**Objetivo de esta conversación:** integrar datos de retail normalizados de CLI Market con el ecosistema de pagos de BCP (incluido Yape) para habilitar comercio agéntico en Perú.

---

## En una frase

BCP tiene la mayor base de usuarios de pagos digitales del país a través de Yape — CLI Market ya construyó la capa de datos e infraestructura de control (precios normalizados, auditoría, aprobación humana) que ese volumen de usuarios va a necesitar cuando los agentes de IA empiecen a comprar en su nombre. Proponemos una integración de datos, no un conector que compita con Yape ni con el riel de BCP.

---

## Por qué BCP específicamente

BCP, vía Yape, tiene la distribución más masiva de pagos digitales de Perú, con fuerte penetración en PyMEs y consumidores. Es también el actor con el estándar de compliance/riesgo más alto de los tres — como banco regulado, cualquier flujo de cobro agéntico que evalúen va a exigir exactamente lo que CLI Market ya construyó: auditoría inmutable y aprobación humana obligatoria antes de cualquier movimiento de dinero.

Hoy CLI Market ya expone Yape/Plin como método de pago manual (QR estático) dentro de su propio flujo de checkout — es decir, ya hay usuarios de CLI Market pagando hacia números Yape. Eso no es una integración PSP real todavía, pero es evidencia de demanda existente del lado de CLI Market para formalizar ese riel.

**Riel de pago y distribución masiva → los tiene BCP/Yape. Datos de retailers normalizados → los tiene CLI Market.**

---

## Qué aporta CLI Market (con track record, no en papel)

| Activo | Estado |
|---|---|
| Datos normalizados de retail | Golden Records (`prod_*`) que unifican productos entre múltiples retailers peruanos y de la región, con historial de precios y stock. |
| Servidor MCP en producción | 40+ tools ya operando (búsqueda, comparación, checkout, alertas de precio), consumido activamente por agentes hoy. |
| Auditoría de escrituras | Audit log append-only ya en producción — trazabilidad completa, un requisito no negociable para cualquier flujo que toque un banco regulado. |
| Control humano en el loop | Mecanismo de aprobación humana (requester/approver, expiración, umbral por monto) ya implementado y funcionando en un producto real (Procure Copilot) — directamente relevante para el estándar de riesgo de BCP. |
| Track record de pagos en Perú | Integración real con MercadoPago Perú ya en producción, más el flujo Yape/Plin manual ya usado por usuarios de CLI Market hoy. |
| Modelo de cuentas de negocio | Soporte para organizaciones multi-usuario, relevante para el segmento PyME que Yape ya atiende masivamente. |

---

## Qué buscamos de BCP

- Una conversación con el equipo de innovación/Yape para evaluar formalizar el riel Yape/Plin dentro de CLI Market (hoy manual) como integración API real.
- Entender los requisitos de compliance y el proceso de evaluación de BCP como entidad regulada — asumimos el ciclo más largo y riguroso de los tres PSPs.
- Explorar si BCP quiere consumir el feed de datos normalizados de CLI Market para dar contexto a sus propios flujos de cobro agéntico dentro del ecosistema Yape.

## Qué NO estamos proponiendo

- No buscamos construir un conector que agregue BCP/Yape junto a otros PSPs peruanos.
- No buscamos custodiar fondos, credenciales, ni datos financieros de usuarios de BCP/Yape — el riel y la relación regulatoria siguen siendo 100% de BCP.

---

## Siguiente paso

Dado el perfil de banco regulado, proponemos una llamada exploratoria (30-45 min) con el equipo de innovación/Yape, con foco inicial en formalizar el riel Yape/Plin ya usado hoy por usuarios de CLI Market, antes de escalar a una conversación de partnership de datos más amplia.

**Contacto:** Ricardo Cuba Alván — CLI Market (cli-market.dev)
