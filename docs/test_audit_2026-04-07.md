# Test Audit — 2026-04-07

## Alcance

Auditoría manual y runtime del proyecto `funding arb` en:

- `/home/alan/Documentos/arbitraje`

Objetivo:

- levantar servicios locales
- probar endpoints seguros
- probar flujo paper
- detectar errores, bugs, inconsistencias y falencias operativas

## Entorno usado

- Python del proyecto: `./.venv/bin/python`
- Server API activo en `127.0.0.1:8790`
- Private backend activo en `127.0.0.1:8787`
- Fecha de prueba: `2026-04-07`

## Pruebas ejecutadas

### Servicios

- `curl http://127.0.0.1:8790/health`
- `curl http://127.0.0.1:8787/health`
- `./.venv/bin/python funding_arb_bot.py env-check`
- `curl http://127.0.0.1:8790/api/doctor`

### Endpoints de lectura

- `curl http://127.0.0.1:8790/api/assets`
- `curl http://127.0.0.1:8790/api/inspect/BTC?hours=24`
- `curl http://127.0.0.1:8790/api/scan?hours=24&top=5`
- `curl http://127.0.0.1:8790/api/status`
- `curl http://127.0.0.1:8790/api/balances`
- `curl http://127.0.0.1:8787/api/private/summary`

### Flujo paper / operativo seguro

- `./.venv/bin/python funding_arb_bot.py pre-trade-check BTC --hours 24`
- `./.venv/bin/python funding_arb_bot.py paper-open XLM --hours 24 --notional 100 --force`
- `./.venv/bin/python funding_arb_bot.py paper-close XLM --hours 24`
- `POST /api/prepare-trade` para `XLM`
- `POST /api/execute-trade` con `kill_switch_active = true` para verificar rechazo seguro
- `./.venv/bin/python test_smoke.py`

## Resultado general

El proyecto está funcional en lectura, scan, inspect, doctor, paper trading y preparación de trades.

No se ejecutaron órdenes reales con `kill_switch_active = false`.

Se detectaron fallas reales y luego se corrigieron en esta misma sesión.

## Estado post-fix

Validación posterior a las correcciones:

- `GET /api/balances`: OK
- `GET /api/private/lighter`: OK
- `pre-trade-check XLM --hours 24`: OK
- `paper-open XLM --hours 24 --notional 50 --force`: OK
- `GET /api/status`: OK
- `paper-close XLM --hours 24`: OK
- `POST /api/prepare-trade` vía API: OK
- `test_smoke.py`: OK

Cambios resueltos:

- balances con `hl_available` poblado y desglose por cuenta
- logging API para `prepare-trade` y rechazos de `execute-trade`
- alineación de `hold_hours` con `hours` en `inspect`, `pre-trade-check` y `paper-open`
- smoke test con restauración segura del kill switch y selección dinámica de símbolo
- backend privado sin `api_public_key` ni `api_keys` en la respuesta

## Hallazgos

### 1. `hl_available` estaba roto y el endpoint de balances mezclaba conceptos de equity

Severidad original: Alta

Estado: Corregido

Archivo:

- `funding_arb_server.py:199-246`

Problemas originales:

- `result["hl_available"]` se inicializa pero nunca se asigna al resultado final.
- el valor que se calcula para `hl_available` proviene de `margin.get("totalMarginUsed", 0)`, que semánticamente no es "available", sino margen usado.
- el endpoint revisa varias direcciones (`HYPERLIQUID_ACCOUNT_ADDRESS`, `LIGHTER_WALLET_ADDRESS`, `HYPERLIQUID_AGENT_WALLET_ADDRESS`) y consolida el máximo como `hl_equity`, lo que puede mezclar fondos de contextos distintos bajo una sola etiqueta.

Evidencia previa:

- `/api/balances` devolvió `{"hl_equity":2899.70941142,"hl_available":null,"lt_equity":81.5575,"total":2981.26691142}`
- `/api/private/summary` mostró para Hyperliquid `margin_summary.accountValue = "0.0"`

Fix aplicado:

- `funding_arb_server.py` ahora:
  - asigna `result["hl_available"]`
  - usa `withdrawable` en lugar de `totalMarginUsed`
  - expone `hl_accounts` con detalle por address
  - suma el total en vez de elegir silenciosamente la cuenta de mayor valor

Evidencia post-fix:

- `/api/balances` devolvió:
  - `hl_available: 0.0`
  - `hl_spot_usdc: 2899.70941142`
  - `hl_accounts: [...]`

Impacto residual:

- el naming `hl_equity` sigue consolidando spot+perps bajo un resumen común
- eso ya es mucho más transparente porque ahora viene con desglose por cuenta

### 2. Los endpoints API de `prepare-trade` y `execute-trade` no quedaban auditados en `events`

Severidad original: Media-Alta

Estado: Parcialmente corregido

Archivos:

- `funding_arb_server.py:151-164`
- `funding_arb_bot.py:1650-1661`

Problema original:

- la CLI sí registra `prepare_trade` y `execute_trade` mediante `log_event(...)`
- la API HTTP llama directo a `bot.prepare_trade(...)` y `bot.execute_trade(...)` sin pasar por los wrappers que loguean

Evidencia previa:

- se llamó `POST /api/prepare-trade` para `XLM` y devolvió un `plan_id` válido
- al consultar `arb_bot.db`, la tabla `events` no mostró ningún evento `prepare_trade` nuevo asociado a esa llamada HTTP

Fix aplicado:

- `funding_arb_server.py` ahora registra:
  - `prepare_trade`
  - `prepare_trade_rejected`
  - `execute_trade`
  - `execute_trade_rejected`

Evidencia post-fix:

- `arb_bot.db` mostró:
  - `prepare_trade {"via": "api", ...}`

Impacto residual:

- `paper-open` y `paper-close` vía API ya quedaban auditados porque reutilizan los comandos CLI
- sería razonable loguear también cambios de kill switch si se quiere auditoría completa de control operativo

### 3. Había una inconsistencia fuerte entre `hours` y `hold_hours`

Severidad original: Media

Estado: Corregido en flujo interactivo y paper

Archivos:

- `config.json:2-16`
- `funding_arb_bot.py:973`
- `funding_arb_bot.py:1462-1464`

Problema original:

- el usuario puede pedir `--hours 24` y la señal se calcula con histórico de 24h
- pero el resultado económico esperado se proyecta usando `config.scan.hold_hours`, que hoy está en `720`

Evidencia previa:

- `paper-open XLM --hours 24` guardó:
  - `hours_basis = 24`
  - `hold_hours = 720`

Fix aplicado:

- `Bot.resolve_hold_hours()` usa el `hours` pedido por el usuario
- `inspect()`, `pre_trade_check()` y `paper_open()` quedaron alineados con ese valor
- `pre_trade_check` ahora devuelve `analysis_hours` y `hold_hours`

Evidencia post-fix:

- `pre-trade-check XLM --hours 24` devolvió `hold_hours: 24`
- `paper-open XLM --hours 24` guardó `hold_hours: 24`

Impacto residual:

- `scan()` global sigue usando el hold implícito de su propia invocación y config del flujo existente
- si se quiere máxima claridad, el frontend debería mostrar explícitamente el horizonte de hold usado en cada tarjeta

### 4. `test_smoke.py` modificaba configuración viva y no garantizaba rollback

Severidad original: Media

Estado: Corregido

Archivo:

- `test_smoke.py:35-58`

Problemas originales:

- activa `kill_switch_active = true`
- solo lo vuelve a desactivar al final, sin `try/finally`
- si el script se interrumpe o falla entre medio, puede dejar el sistema bloqueado
- además usa `BTC` fijo para `prepare-trade`, aunque esa preparación puede fallar legítimamente según mercado/config, haciendo que el smoke no sea determinista

Evidencia previa:

- el smoke corrió y `prepare-trade BTC` devolvió:
  - `{"detail":"No se puede preparar trade para BTC. Pre-trade check falló."}`

Fix aplicado:

- ahora usa `try/finally`
- restaura el estado original del kill switch
- elige un símbolo dinámicamente desde `scan`
- amplía timeout de `scan` a 45s para evitar falsos negativos por timeout artificial

Evidencia post-fix:

- `./.venv/bin/python test_smoke.py` terminó con:
  - `/api/prepare-trade OK para XLM`
  - `/api/execute-trade aborto por kill switch verificado`
  - `Smoke tests terminados`

Impacto residual:

- sigue siendo un smoke sobre entorno vivo, no un test aislado
- idealmente debería existir un modo sandbox/config de test

### 5. El backend privado exponía metadatos sensibles de cuenta sin capa adicional de auth

Severidad original: Media

Estado: Parcialmente corregido

Archivo:

- `private_backend.py:72-121`
- `private_backend.py:161-173`

Problema original:

- `/api/private/lighter` devuelve `api_public_key` y lista de `api_keys`
- `/api/private/summary` expone lo mismo junto con estado de cuenta
- no hay autenticación propia; la protección real es solo "escuchar en localhost"

Fix aplicado:

- `/api/private/lighter` ya no devuelve:
  - `api_public_key`
  - `api_keys`

Evidencia post-fix:

- la respuesta actual conserva:
  - `account_index`
  - `api_key_index`
  - `auth_token_ready`
  - `account`, `sub_accounts`, `pnl_7d`, `position_funding`

Impacto residual:

- sigue sin haber auth adicional a localhost
- para uso personal puede ser aceptable, pero sigue siendo una superficie local sensible

Nota:

- no se detectó exposición de `api_private_key` en la respuesta
- sí se detectó sobreexposición de metadata que no parece necesaria para la UI operativa

### 6. El scan depende de APIs externas ruidosas y eso ya aparece en la base de eventos

Severidad: Baja-Media

Estado: Vigente

Archivo:

- `funding_arb_bot.py:1192-1232`

Evidencia de runtime:

- en `events` aparecieron scans recientes con errores como:
  - `HTTP Error 429: Too Many Requests`
  - `HTTP Error 400: Bad Request`
  - `HTTP Error 500: Internal Server Error`

Observación:

- el bot logra devolver resultados aun con esos errores parciales
- eso es bueno para resiliencia, pero hoy no hay una capa más clara de agregación/alerta para distinguir "scan exitoso" de "scan exitoso con degradación fuerte"

Impacto:

- oportunidades potencialmente ausentes del ranking sin que quede obvio en la UI
- resultados menos estables bajo rate limit

## Comportamientos verificados como correctos

- `/health` del server principal responde bien
- `/health` del backend privado responde bien
- `/api/doctor` marcó `ok=true`
- `/api/inspect/BTC?hours=24` devolvió payload consistente
- `/api/scan?hours=24&top=5` devolvió ranking válido
- `paper-open XLM` abrió posición paper correctamente
- `/api/status` reflejó la posición abierta
- `paper-close XLM` cerró la posición correctamente
- `POST /api/prepare-trade` para `XLM` generó `plan_id`
- `POST /api/execute-trade` con kill switch activo rechazó la ejecución como corresponde
- `POST /api/prepare-trade` ahora deja evento en `arb_bot.db`
- `paper-open XLM --hours 24` ahora persiste `hold_hours = 24`

## Limitaciones de esta auditoría

- no se probaron órdenes reales con kill switch desactivado
- no se probó reconciliación post-trade real
- no se hicieron tests automatizados unitarios o de integración completos
- no se revisó frontend HTML en navegador visual, solo comportamiento de APIs que lo alimentan

## Riesgos remanentes y recomendaciones

1. Agregar logging también para cambios de kill switch y, si hace falta, para `paper-open/close` rechazados vía API.
2. Considerar auth local simple o token compartido para `private_backend.py` si el servicio va a quedar residente.
3. Exponer en el frontend el `hold_hours` real usado por cada señal para evitar ambigüedad operativa.
4. Mejorar la visibilidad de degradación del scan cuando haya `429/400/500` parciales en proveedores externos.
