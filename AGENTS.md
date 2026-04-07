# AGENTS.md

Guia operativa para trabajar en este repo con varios agentes y CLIs.

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

## Workflow Recomendado

- Una rama por cambio: `feat/...`, `fix/...`, `docs/...`, `refactor/...`
- Un agente implementa.
- Otro agente revisa riesgo, naming y regresiones.
- Si el cambio es grande, actualizar `TASKS.md` antes y despues.

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

## Criterio de Terminado

Un cambio esta realmente listo cuando:

- el objetivo quedo reflejado en codigo o docs
- el comando de verificacion apropiado fue ejecutado, o se deja dicho por que no
- no entraron secretos, DBs ni logs al repo
- `CURRENT_STATE.md` y `TASKS.md` siguen representando la realidad
