# Memory

Memoria técnica y operativa de este proyecto.

## Decisiones vigentes

- La fuente de verdad operativa es SQLite en `arb_bot.db`.
- El repo público no debe incluir `.env`, `config.json`, DBs, logs ni snapshots legacy de runtime.
- El workflow estándar del repo usa `just`, `ruff` y `pytest`.
- El objetivo inmediato no es sumar features grandes, sino mejorar testabilidad y seguridad de cambio antes de `v0.6`.

## Restricciones importantes

- `prepare-trade` y `execute-trade` son superficie sensible.
- `private_backend.py` toca credenciales y contexto de cuenta; no cambiar sin entender el impacto.
- El smoke test toca entorno local vivo; no reemplaza tests aislados.
- Gran parte del comportamiento real depende de APIs de Hyperliquid y Lighter, por lo que conviene maximizar pruebas puras o con mocks.

## Lecciones aprendidas

- Si el repo va a ser público, hay que sanitizar auditorías y artefactos de runtime antes de compartirlo.
- Tener `AGENTS.md`, `START_HERE.md`, `CURRENT_STATE.md` y `TASKS.md` reduce mucho el costo de handoff entre agentes.
- Un lint demasiado estricto sobre código heredado mete ruido; conviene empezar con reglas de alta señal y endurecer después.

## Próximo foco

- tests unitarios sobre cálculo de señales
- tests de integración local para FastAPI
- slice chico de reconciliación post-trade para `v0.6`
