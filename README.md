# Funding Arb Bot MVP

MVP en modo `paper` para:

- escanear oportunidades entre `Hyperliquid` y `Lighter`
- estimar edge neto después de fees y slippage
- proponer la dirección correcta del trade
- abrir/cerrar posiciones simuladas con estado persistente

No envía órdenes reales. El objetivo es validar lógica y flujo operativo antes de tocar capital.

## Estado actual

Ultima revision: 2026-04-06

Estado real del proyecto hoy:

- `v0.1`: cumplido
- `v0.2`: cumplido
- `v0.3`: avanzado
- `v0.4`: funcional (dashboard web con stats en tiempo real)
- `v0.5`: funcional (preparación y ejecución real asistida)

Hoy el sistema ya puede:

- escanear oportunidades con filtros operativos
- inspeccionar activos con funding historico alineado
- correr `pre-trade-check` con libros frescos
- abrir y cerrar posiciones `paper`
- calcular PnL paper con funding, costo y drift
- exponer todo via FastAPI y dashboard HTML
- **ejecutar check de liquidez final ('prepare-trade') contra orderbooks L2**
- **enviar órdenes reales a mercado (HL y Lighter) tras una confirmación manual ('execute-trade')**
- **proteger el entorno con un Kill Switch global en caliente (`kill_switch_active`)**

Hoy el sistema todavia no puede:

- operar de manera 100% autónoma sin clics humanos
- ejecutar hedge correctivo inteligente (cancel & replace si la 2da pata falla parcialmente)
- re-balancear posiciones o salir automáticamente al acercarse al Take Profit / Stop Loss

## Documentación y Arquitectura

Para auditores, desarrolladores nuevos y resolución de problemas, el proyecto cuenta con el siguiente mapa de documentos:

- 🏗️ **[ARCHITECTURE.md](docs/ARCHITECTURE.md)**: El mapa técnico definitivo del bot. Cómo el Dashboard, el Bot Server y el Private Backend interactúan, incluyendo seguridad, base de datos SQLite y mitigación de baneos de IP (Rate Limits).
- 🛣️ **[ROADMAP.md](docs/ROADMAP.md)**: Visión a futuro e implementaciones pendientes para arquitectura institucional.
- 📜 **[CHANGELOG.md](docs/CHANGELOG.md)**: Bitácora de parches y updates recientes.
- 🚶 **[Walkthrough (v0.5)](docs/walkthrough.md)**: Registro visual de la transición de paper-trading a ejecución real.
- 🛠️ **[EXTERNAL_TOOLS.md](docs/EXTERNAL_TOOLS.md)**: Guía y ecosistema de aplicaciones externas gratuitas sugeridas (Docker, PM2, Sentry) para escalar, resilir y proteger al bot.

### Estructura de Directorios

- **`/` (Core Run-Time):** 
  - `funding-arb (1).html` (Frontend Dashboard SPA)
  - `funding_arb_server.py` (Capa REST de Orquestación FastAPI Puerto 8790)
  - `funding_arb_bot.py` (Capa de Lógica Matemática, Storage y Caching)
  - `private_backend.py` (Capa de Ejecución, Billeteras y Lighter Node Puerto 8787)
  - `exchange_adapters.py` (Drivers de Ejecución Oficiales para envíos)
  - `arb_bot.db` (Fuente de Verdad en SQLite)
- **`/docs`**: Toda la documentación auditada del sistema, implementaciones y mapas.
- **`/assets/images`**: Material referencial capturado en vivo del bot funcionando.
- **`/data/legacy`**: Archivos de estado plano (`.json`) obsoletos.

## Idea operativa

El bot compara el funding horario de ambos venues:

- si `HL - LT > 0`, HL paga más
  - trade sugerido: `long Lighter / short HL`
- si `HL - LT < 0`, Lighter paga más
  - trade sugerido: `long HL / short Lighter`

La señal no usa solo spread bruto. También mira:

- consistencia de dirección
- drawdown histórico del carry
- muestras disponibles
- costo esperado de entrada/salida
- slippage asumido
- ventana objetivo de permanencia (`hold_hours`)

## Uso

Copiá el config base:

```bash
cp config.example.json config.json
```

Escanear oportunidades:

```bash
python3 funding_arb_bot.py scan
python3 funding_arb_bot.py scan --hours 24 --top 15
```

Inspeccionar un activo:

```bash
python3 funding_arb_bot.py inspect TRUMP
python3 funding_arb_bot.py inspect ZEC --hours 168
```

Abrir una posición paper:

```bash
python3 funding_arb_bot.py paper-open TRUMP --notional 1000
```

Ver estado:

```bash
python3 funding_arb_bot.py status
```

Diagnóstico:

```bash
python3 funding_arb_bot.py doctor
```

Chequeo pre-trade con libros fresh:

```bash
python3 funding_arb_bot.py pre-trade-check ZEC --hours 24
```

Cerrar posición paper:

```bash
python3 funding_arb_bot.py paper-close TRUMP
```

## Backend privado local

Este backend corre del lado servidor local y usa el `.env`. No expone secrets al navegador.

Levantarlo:

```bash
/home/alan/Documentos/arbitraje/.venv/bin/python /home/alan/Documentos/arbitraje/private_backend.py
```

Health:

```bash
curl http://127.0.0.1:8787/health
```

Resumen privado:

```bash
curl http://127.0.0.1:8787/api/private/summary
```

Endpoints:

- `/api/private/hyperliquid`
- `/api/private/lighter`
- `/api/private/summary`

## Qué calcula

Para cada activo:

- `gross_apy`
  - edge bruto anualizado por funding
- `net_est_apy`
  - edge anualizado después de costos estimados
- `expected_net_pct_hold`
  - retorno esperado para la ventana `hold_hours`
- `consistency_pct`
  - qué tan seguido la dirección del spread se mantuvo
- `max_drawdown_bps`
  - drawdown histórico del carry acumulado
- `trade`
  - combinación de patas sugerida

## Estado local

El bot ahora guarda en SQLite:

- posiciones paper
- cache de históricos
- cache de order books
- cache incremental de scans
- event log (`scan`, `inspect`, `paper_open`, `paper_close`, `doctor`)

La fuente de verdad viva es solo SQLite.

- `paper_state.json` y `scan_cache.json` ya no se usan como estado operativo
- si existian, se migran una vez y se renombran a `data/legacy/*.legacy.json`

## Cache y datos fresh

- `scan` e `inspect` pueden reutilizar cache para historicos y order books
- `pre-trade-check` fuerza order books fresh y valida backend privado live
- para ejecucion real, la decision final no debe apoyarse en cache

## Observabilidad y errores

- `funding_arb_bot.py` mantiene una tabla `error_logs` en SQLite y escribe cada excepción en `logs/bot_errors.ndjson`.
- Nuevo comando `python3 funding_arb_bot.py recent-errors --limit 20` expone los últimos errores con contexto (fuente, operación, código HTTP, payload, run_id).
- La API local ofrece `/api/errors?limit=N` y el dashboard ahora muestra los últimos 6 errores en una tarjeta en la parte inferior.
- `/api/errors` se activa desde el servidor FastAPI y es consumido por el dashboard para ayudar a detectar rápidamente rate limits, `400/500` y fallos de conectividad.

## Supuestos importantes

- `Hyperliquid` histórico viene firmado por `fundingRate`
- `Lighter` histórico usa `rate` en porcentaje y el signo viene por `direction`
- el bot usa muestras de `1h`
- el cálculo neto depende fuerte de tus supuestos de fee/slippage

## Próximos pasos para pasar a real

1. Realizar primeras pruebas de ejecución de muy bajo capital en `v0.6` para validar latencia limit-orders vs market.
2. Implementar reconciliación contra exchange después de cada orden para actualizar la base de datos local de posiciones.
3. Crear hedge correctivo inteligente en caso de _fill_ parcial en el L2.
4. Diseñar alertas y cierres semi-asistidos.
