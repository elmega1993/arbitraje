# Testing

## Niveles de prueba

### 1. Unitarias seguras

Objetivo:

- validar logica pura
- no depender de APIs reales
- correr rapido y seguido

Ubicacion esperada:

- `tests/`

Casos candidatos:

- normalizacion de simbolos
- aliases HL/Lighter
- reglas de retry
- calculos y helpers puros

### 2. Integracion local

Objetivo:

- validar interaccion entre modulos del repo
- idealmente con mocks o fixtures

Casos candidatos:

- endpoints de FastAPI con cliente de test
- logging de eventos
- persistencia SQLite con DB temporal

### 3. Smoke operativo

Archivo actual:

- `test_smoke.py`

Uso:

- validar que el stack local esta levantado
- tocar endpoints reales y estado operativo controlado

No usar como unico sustituto de tests unitarios.

## Regla Practica

Antes de tocar logica sensible:

- correr unitarias si existen
- correr `ruff`
- usar smoke solo cuando el cambio toca integracion o flujo operativo

## Comandos

```bash
just lint
just test
just smoke
```
