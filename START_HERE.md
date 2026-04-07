# Start Here

Si sos un agente entrando sin contexto, empezá por acá.

## 1. Qué es este repo

Proyecto de arbitraje de funding entre Hyperliquid y Lighter.

Incluye:

- scanner de oportunidades
- inspect por activo
- paper trading
- pre-trade checks con orderbooks
- ejecucion real asistida con confirmacion manual
- dashboard HTML
- APIs locales FastAPI

## 2. Archivos a leer primero

Leé en este orden:

1. `AGENTS.md`
2. `CURRENT_STATE.md`
3. `TASKS.md`
4. `MEMORY.md`
5. `docs/ARCHITECTURE.md`
6. `docs/TESTING.md`

## 3. Archivos principales del sistema

- `funding_arb_bot.py`: logica core
- `funding_arb_server.py`: API publica local
- `private_backend.py`: backend privado local
- `exchange_adapters.py`: adaptadores de ejecucion
- `funding-arb (1).html`: dashboard

## 4. Que no tocar sin pedido explicito

- `.env`
- `config.json`
- `arb_bot.db`
- logs
- cualquier flujo de orden real

## 5. Estado actual

El proyecto esta en una etapa `v0.5` funcional con:

- paper trading operativo
- prepare-trade
- execute-trade asistido
- kill switch

La prioridad actual no es inventar features al azar, sino mejorar workflow, testing y preparar el camino a `v0.6`.

## 5.1 Qué hacer primero al entrar

Si no tenés una tarea explícita:

1. leer `CURRENT_STATE.md`
2. tomar un ítem de `TASKS.md`
3. elegir el cambio más chico que mueva ese ítem
4. correr la verificación mínima necesaria
5. actualizar docs si cambió el estado real

## 6. Comandos utiles

Si existe `.venv`:

```bash
source .venv/bin/activate
```

Checks base:

```bash
just lint
just test
```

Servicios:

```bash
just server
just private
```

Smoke:

```bash
just smoke
```

## 7. Como trabajar bien

- Hacé cambios chicos y verificables.
- Si tocás riesgo operativo, documentalo.
- Si proponés algo grande, primero anotá el objetivo en `TASKS.md`.
- No asumas acceso a secretos ni a entorno real si no está confirmado.
- Si encontrás una restricción duradera del proyecto, escribila en `MEMORY.md`.

## 8. Qué entregar

Cuando termines:

- explicar qué cambiaste
- decir qué verificaste
- mencionar riesgos o cosas no verificadas
