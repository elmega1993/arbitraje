# Mapa de Arquitectura y Sistema (Funding Arb Bot)

Este documento es la guía técnica definitiva del sistema. Detalla cómo se conectan los módulos, el flujo de datos y la gestión de estado para facilitar futuras auditorías y desarrollo continuo.

## Vista General del Ecosistema

El bot está diseñado en un modelo de **Tres Capas (Tiers)** para preservar la seguridad de las claves privadas (aislando al navegador de las credenciales L1/L2) y mejorar la robustez frente a bloqueos de los exchanges (Rate Limits / 429).

1. **Frontend (Capa de Presentación)**
   - **Archivo:** `funding-arb (1).html`
   - **Rol:** Dashboard SPA (Single Page Application) estático que corre en el navegador (Glassmorphism UI).
   - **Mecánica:** Hace *polling* interactivo consumiendo la REST API del Bot Server (`localhost:8790`). No contiene lógica de enrutamiento crítico, solo representación visual, preálculo de fallbacks y gestión de modales (Pre-Trade Check, Kill Switch).

2. **Bot Server (Capa de Orquestación y Lógica Core)**
   - **Archivos:** `funding_arb_server.py`, `funding_arb_bot.py`
   - **Rol:** Corazón algorítmico. Expone una API asincrónica vía FastAPI (`localhost:8790`).
   - **Mecánica:** 
     - `funding_arb_server.py`: Actúa de router HTTP, control de excepciones globales, y orquesta el ThreadPool para tareas bloqueantes.
     - `funding_arb_bot.py`: Posee la clase `Bot` y `MarketData`. Realiza los cálculos cuánticos, maneja el *caching* (fundamental para evitar baneos IP por Rate Limits), y gestiona el flujo CRUD contra la base de datos `arb_bot.db`. Construye las *Señales (Signals)* cruzando el historial de Hyperliquid y Lighter.

3. **Private Backend & Exchange Adapters (Capa de Ejecución)**
   - **Archivos:** `private_backend.py`, `exchange_adapters.py`
   - **Rol:** Aislamiento de seguridad y ejecución L2.
   - **Mecánica:**
     - `private_backend.py`: Servidor FastAPI (`localhost:8787`) que levanta el entorno de variables `.env`. Es el *único* proceso autorizado a leer firmas, llaves privadas y realizar consultas sobre saldos reales en vivo. El *Bot Server* lo consume para obtener `lt_equity` o salud de cuentas.
     - `exchange_adapters.py`: Normaliza las discrepancias de las APIs oficiales. Provee adaptadores estándar (Paper/Real) con métodos unificados como `execute_market_order()`.

---

## Flujo de Datos Críticos

### 1. El Scanner (`/api/scan`)
- **Frontend** llama a `/api/scan?hours=168&top=40`.
- **Server** delega en `bot.scan(hours, top)`.
- El **Bot** verifica si existe un resultado vivo en `arb_bot.db` (`self._load_incremental_scan`). Si existe y el TTL no expiró, lo devuelve (0.1ms).
- Si no existe:
  1. Descarga el universo de Hyperliquid (`bot.common_assets()`).
  2. Lighter Markets: Usa `bot.ensure_lt_markets()` (cacheado en memoria) para no saturar al exchange.
  3. Ejecuta _ThreadPoolExecutor_ (6 workers) mapeando historiales vía `fetch_hl_history_cached` y `fetch_lt_history_cached` (para mitigar 429 Too Many Requests).
  4. Aplica lógica matemática (Spread, Drawdown, Consistency) -> Filtra -> Calcula slippage real usando el Orderbook L2 -> Ordena y graba en SQLite.

### 2. Ejecución de Órdenes (`/api/prepare-trade` y `/api/execute-trade`)
Para evitar enviar órdenes ciegas, el sistema impone un **protocolo de 2 pasos**:

**Paso 1: `prepare-trade`**
- Toma el Activo y Volumen (Notional).
- Comprueba si el `kill_switch_active` está activado en `config.json`. Si lo está, aborta.
- Descarga Orderbooks L2 "frescos" sin cache.
- Verifica si el _slippage_ destruye el trade. Si el slippage es seguro, crea un **Trade Plan**.
- Graba el Trade Plan en la tabla SQLite `trade_plans` con el estado `pending`.
- Retorna el Plan ID al Frontend.

**Paso 2: `execute-trade`**
- Frontend envía una confirmación manual con el `Plan ID`.
- El bot levanta el plan de la DB, constata que no esté expirado (< 5 minutos desde la preparación).
- Invoca los `ExchangeAdapters` enviando órdenes reales (Market/IOC).
- Extrae resultados (Fills) y marca el plan como `executed` o `failed` en SQLite.

---

## Persistencia y Estado (SQLite `arb_bot.db`)
El bot ya no depende de archivos `.json` temporales, unificando todo en SQLite para asegurar atomicidad.

Tablas Core:
- `cache`: Almacena binarios ox/json de Orderbooks y Funding History. Previene Rate Limits.
- `positions`: Registra posiciones "Paper" y métricas de Drift/Hedge simulado.
- `trade_plans` y `trade_executions`: Logs inmutables de auditoría de cada trade ejecutado en dinero real.
- `events`: Log crudo de eventos importantes (diagnósticos de caída, scan runs).

## Failsafes y Medidas Institucionales Actuales

1. **Kill Switch Global:** Corta el circuito pre-trade al instante impidiendo nuevas creaciones de _Trade Plans_.
2. **Control de Rate Limits (Anti-HTTP 429):** Integrado en `funding_arb_server.py`. El endpoint de `/health` fue abstraído para no interrogar a la red Lighter en cada llamado. Los activos base viajan bajo memoria cacheada localmente. Las fallas no interceptadas del bot se envían directo al registro `.bot_app.log`.
3. **Double-Checked Lock:** La inicialización de base de datos y memoria usan `threading.Lock` para que los múltiples pedidos del frontend nunca corrompan lecturas o escrituras.

## Links Rápidos para el Auditor
- ¿Querés ver el cálculo matemático de rentabilidad base? -> Ver `calc_signal()` en [funding_arb_bot.py](../funding_arb_bot.py)
- ¿Querés ver cómo se envía la firma real de una orden? -> Ver `LighterAdapter.execute_market_order()` en [exchange_adapters.py](../exchange_adapters.py)
- ¿Querés monitorear los errores crudos en vivo? -> Revisa o corre `tail -f bot_app.log`
