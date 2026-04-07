# Current State

Ultima actualizacion: 2026-04-07

## Donde esta parado el proyecto

- `v0.5` funcional: paper, dashboard, pre-trade y ejecucion real asistida
- repo GitHub publico y sanitizado
- workflow base agregado: `AGENTS.md`, `START_HERE.md`, `TASKS.md`, `justfile`, `pyproject.toml`
- lint y tests base funcionando localmente (`ruff` + `pytest`)

## Riesgos Abiertos

- gran parte de la validacion sigue dependiendo de servicios locales y APIs reales
- la suite de tests todavia cubre helpers puros, no flujos de integracion importantes
- el paso a `v0.6` requiere reconciliacion post-trade y manejo de fill parcial real

## Prioridad Actual

Usar el workflow ya armado para subir la cobertura tecnica del repo sin tocar el entorno real innecesariamente.

## Proximo Paso Recomendado

Agregar:

- tests unitarios para calculo de señales y payloads
- tests de integracion local para FastAPI con DB temporal
- primer slice de `v0.6` con reconciliacion post-trade
