# Funding Rate Arbitrage Bot v0.5 — Upgrade Walkthrough

Se ha completado satisfactoriamente la transición del bot de "papel" (v0.1-v0.4) a **Ejecución Asistida (v0.5)** con dinero real, según el roadmap establecido.

## Resumen de Tareas Completadas

### 1. Estabilización de la Base Actual (Fase 1)
- Ejecuté el servidor v0.4 y corrobore mediante curl y tests que los endpoints `/api/scan`, `/api/inspect`, `/api/status`, y `/api/doctor` funcionaban sin errores.
- Comprobé la consistencia de los datos del UI vs Backend y limpié imports y dependencias ausentes en un lint inicial.

### 2. Capa de Ejecución Real (Fase 2)
- **[exchange_adapters.py](file:///home/alan/Documentos/arbitraje/exchange_adapters.py)**: Nuevo módulo con implementaciones wrapper ([HyperliquidAdapter](file:///home/alan/Documentos/arbitraje/exchange_adapters.py#29-100) y [LighterAdapter](file:///home/alan/Documentos/arbitraje/exchange_adapters.py#102-204)) y [PaperAdapter](file:///home/alan/Documentos/arbitraje/exchange_adapters.py#206-222).
  - Usé la librería oficial [lighter](file:///home/alan/Documentos/arbitraje/private_backend.py#182-185) (la clase `SignerClient.create_market_order_if_slippage`).
  - Usé `hyperliquid.exchange.Exchange(wallet, constants.MAINNET_API_URL).market_open()` para Hyperliquid.
  - El sistema extrae el precio Mid del libro L2 para convertir de USD nocional a "Token Size" con la precisión correcta antes del envío.
- **`trade_plans` y `trade_executions`** (SQLite): Creadas nuevas tablas para el historial y verificación de la planeación y ejecución de ordenes.
- **Preparación → Confirmación → Ejecución**: Se construyeron los endpoints `/api/prepare-trade` y `/api/execute-trade` para no ejecutar ciegamente. 
- **Kill Switch & Límites Duros**: Agregamos al [config.json](file:///home/alan/Documentos/arbitraje/config.json) el toggle `kill_switch_active` y lo integramos con chequeos obligatorios `max_notional_per_asset_usd` y expiración en base de datos.
- **Servidor y URL FIX**: Los endpoints API fueron agregados. Además se descubrió y corrigió un pequeño bug del dashboard que enviaba POST requests de [fetch()](file:///home/alan/Documentos/arbitraje/funding-arb%20%281%29.html#721-730) usando URLs relativas sin el dominio del servidor backend (`BOT_API`).

### 3. Dashboard Web Control
- Inserte la UI en `funding-arb (1).html` que consume toda la nueva info y despliega un modal pre-trade permitiendote leer todo en pantalla antes de dar *click al botón rojo* de ejecución real.
- Agregue el botón Toggle "Kill Switch" que te permite encenderlo o apagarlo (`ALLOWED` vs `BLOCKED`).

## Resultados en UI
Acá hay imágenes registradas desde el sub-agente navegador en vivo en esta sesión de trabajo:

![Dashboard Flow](/home/alan/.gemini/antigravity/brain/79ddb635-b694-4455-8219-1c8ee829fa02/dashboard_trade_flow_1775526292823.webp)

![Pre Trade Execution Plan Modal](/home/alan/.gemini/antigravity/brain/79ddb635-b694-4455-8219-1c8ee829fa02/modal_check_1775529955698.png)

## Siguientes Pasos
¡Estás listo para probar la plataforma!
Te recomiendo primero mantener bajo el volumen (Notional) en el dashboard, elegir una oportunidad y darle `Prepare Trade (Real)`. Verificá si Hyperliquid y Lighter rechazan la ejecución vía log o el UI debido a balances antes de aumentar volumen.

Para uso diario:
```bash
# Lanzar servidor interactivo 
./.venv/bin/python3 -m uvicorn funding_arb_server:app --port 8790
```
Y abrís el dashboard `funding-arb (1).html` como de costumbre.
