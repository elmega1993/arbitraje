# Current State

Ultima actualizacion: 2026-04-07

## Donde esta parado el proyecto

- `v0.5` funcional: paper, dashboard, pre-trade y ejecucion real asistida
- repo GitHub privado creado y conectado
- falta endurecer el workflow de desarrollo y la capa de tests aislados

## Riesgos Abiertos

- gran parte de la validacion sigue dependiendo de servicios locales y APIs reales
- no hay suite de tests aislada con mocks como base rapida de iteracion
- el paso a `v0.6` requiere reconciliacion post-trade y manejo de fill parcial real

## Prioridad Actual

Ordenar el workflow para que varios agentes puedan colaborar sin friccion ni riesgo operativo.

## Proximo Paso Recomendado

Agregar:

- tests unitarios para funciones puras de `funding_arb_bot.py`
- `docs/TESTING.md`
- primer slice de `v0.6` con reconciliacion post-trade
