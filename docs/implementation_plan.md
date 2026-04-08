# Plan de Ejecución de Auditoría Externa

Este plan fusiona los hallazgos críticos de la auditoría arquitectónica ([arbitraje_audit_report.html](file:///home/alan/Documentos/arbitraje/Auditoria-externa/arbitraje_audit_report.html)) con las sugerencias de herramientas ([tool_recommendations.md](file:///home/alan/Documentos/arbitraje/Auditoria-externa/tool_recommendations.md)) en un camino de ejecución secuencial. Se prioriza la mitigación de riesgos inmediatos antes de las mejoras estructurales largas.

## Fase 1: Respuesta Inmediata y Seguridad Crítica (Corto Plazo)

El objetivo de esta fase es evitar catástrofes operativas como la ejecución de órdenes por terceros y la fuga de credenciales.

- [ ] **Restringir CORS**: Modificar `allow_origins=["*"]` en [funding_arb_server.py](file:///home/alan/Documentos/arbitraje/funding_arb_server.py) por una lista blanca de localhost (igual que en backend privado).
- [ ] **Escritura Atómica del Kill Switch**: Modificar el endpoint del kill switch para usar escrituras atómicas en [config.json](file:///home/alan/Documentos/arbitraje/config.json) (con archivos temporales y `os.replace`), sumando un `threading.Lock()` para prevenir corrupción de estado bajo alta concurrencia.
- [ ] **Autenticación en Ejecución**: Implementar middleware de autenticación (Bearer token) en FastAPI para proteger endpoints sensibles (`/api/execute-trade`, `/api/paper/open`, `/api/kill-switch`).
- [ ] **Protección de Repositorio (Pre-commit)**: Configurar `pre-commit` con reglas de `gitleaks` (para prevenir exposición accidental de keys de Hyperliquid) y activar reglas avanzadas de `ruff` (`S` y `B`) para detectar vulnerabilidades comunes y bugs.

## Fase 2: Robustez Operativa y Observabilidad (Mediano Plazo)

El objetivo de esta fase es resolver el bloqueo conceptual hacia la `v0.6`, el cual es la incapacidad del sistema de conciliar órdenes reales y seguirlas en el dashboard.

- [ ] **Tabla de Posiciones Reales (SQLite)**: Crear una tabla `live_positions` (independiente de las `positions` paper) para consolidar el estado de órdenes ejecutadas en ambos exchanges (Hyperliquid y Lighter).
- [ ] **Reconciliación Post-trade**: Implementar lógica para consultar el estado real del portfolio inmediatamente después de emitir `execute-trade`, asegurando el manejo seguro de *fills* parciales y descalces de posición.
- [ ] **Dashboard Profesional**: Integrar `Lightweight Charts` en `funding-arb.html` para visualizar de forma robusta las nuevas posiciones reales e históricas de *funding rate*.

## Fase 3: Deuda Técnica, Arquitectura y Velocidad (Largo Plazo)

El objetivo final es reducir la complejidad del código, el consumo de recursos y profesionalizar la integración.

- [ ] **Desacople Core ([funding_arb_bot.py](file:///home/alan/Documentos/arbitraje/funding_arb_bot.py))**: Refactorizar el mega-archivo dividiéndolo en módulos (`db.py`, `signals.py`, `market_data.py`, `cli.py`).
- [ ] **Optimización de Caché**: Refactorizar `save_scan_cache()` para que use `INSERT OR REPLACE` (upserts) en lugar de borrar y reescribir masivamente, aliviando la contención en base de datos.
- [ ] **Pila Asincrónica Unificada**: Eliminar `_run_async_in_thread()` para el adaptador de Lighter y usar el event-loop principal o asincronía nativa de extremo a extremo, previniendo explosiones de *threads* concurrentes.
- [ ] **Modernización de Stack (uv y orjson)**: Migrar el manejador de dependencias a `uv` e implementar `orjson` en la API (incrementando drásticamente el *parsing* de JSONs pesados).
- [ ] **Tipado Estricto**: Integrar `pyright` o `mypy` en el CI/CD (Github Actions) para detectar bugs lógicos antes del deploey.

## Criterios de Éxito y Recomendación

> [!CAUTION]
> Es mandatorio completar **TODA LA FASE 1** de forma secuencial y obligatoria antes de operar con un capital mayor a la asignación de prueba actual. Cualquier de estos fallos expone los fondos a desastres de ejecución local.
