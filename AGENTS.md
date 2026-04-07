# AGENTS.md

Guia operativa para trabajar en este repo con varios agentes y CLIs.

## Bootstrap Obligatorio

Si entrás sin contexto, leé en este orden:

1. `START_HERE.md`
2. `CURRENT_STATE.md`
3. `TASKS.md`
4. `MEMORY.md`
5. `docs/ARCHITECTURE.md`
6. `docs/TESTING.md`

## Objetivo del Repo

Bot de arbitraje de funding entre Hyperliquid y Lighter con:

- research y scanner
- paper trading
- pre-trade checks con orderbooks L2
- ejecucion real asistida con confirmacion manual
- dashboard HTML y APIs FastAPI

## Mapa Rapido

- `funding_arb_bot.py`: logica core, storage SQLite, scanner, inspect, paper, trade planning
- `funding_arb_server.py`: API publica local en `127.0.0.1:8790`
- `private_backend.py`: API privada local en `127.0.0.1:8787`
- `exchange_adapters.py`: ejecucion real y adaptadores por exchange
- `funding-arb (1).html`: dashboard operativo
- `docs/`: arquitectura, roadmap, changelog, auditorias

## Reglas de Trabajo

- No tocar `.env`, `config.json`, `arb_bot.db` ni logs salvo pedido explicito.
- No ejecutar ordenes reales ni desactivar protecciones sin pedido explicito.
- Tratar `prepare-trade` y `execute-trade` como superficie sensible.
- Si un cambio afecta riesgo operativo, documentarlo en `docs/CHANGELOG.md` o `CURRENT_STATE.md`.
- Si un cambio rompe compatibilidad con el dashboard o endpoints, dejarlo anotado.
- Si cambiás el estado del proyecto o la prioridad activa, actualizá `CURRENT_STATE.md` y `TASKS.md`.
- Si descubrís una lección técnica o una restricción duradera, escribila en `MEMORY.md`.

## Workflow Recomendado

- Una rama por cambio: `feat/...`, `fix/...`, `docs/...`, `refactor/...`
- Un agente implementa.
- Otro agente revisa riesgo, naming y regresiones.
- Si el cambio es grande, actualizar `TASKS.md` antes y despues.
- Si hay trabajo en paralelo, preferir `git worktree` en vez de varios agentes sobre el mismo directorio.

## Reparto Sano Entre Agentes

- Codex: implementacion, refactor y cambios concretos de archivos.
- Claude Code: auditoria, review, riesgos, lectura transversal.
- Antigravity u otros: exploracion, lluvia de ideas, propuestas, borradores.

## Comandos Base

- `just setup`: instala tooling dev basico en la venv activa
- `just server`: levanta API publica
- `just private`: levanta backend privado
- `just smoke`: corre smoke operativo
- `just doctor`: corre chequeo de entorno del bot
- `just fmt`: formatea con Ruff
- `just lint`: lint con Ruff
- `just test`: pytest

## Prioridad Actual

La prioridad actual es endurecer el workflow y la testabilidad del repo para avanzar a `v0.6` con menos riesgo.

Primero:

- tests rapidos y aislados
- documentación operativa clara
- slices chicos y verificables

Despues:

- reconciliacion post-trade
- manejo de fill parcial real
- mejoras de ejecucion institucional

## Criterio de Terminado

Un cambio esta realmente listo cuando:

- el objetivo quedo reflejado en codigo o docs
- el comando de verificacion apropiado fue ejecutado, o se deja dicho por que no
- no entraron secretos, DBs ni logs al repo
- `CURRENT_STATE.md` y `TASKS.md` siguen representando la realidad
- si hubo aprendizaje persistente, `MEMORY.md` tambien se actualizo
