# Roadmap

## Estado actual

Ultima revision: 2026-04-07

Lectura honesta del proyecto hoy:

- `v0.1`: mayormente cumplido
- `v0.2`: muy avanzado
- `v0.3`: avanzado
- `v0.4`: panel operativo web listo y funcional
- `v0.5`: ejecución real asistida completada y funcional

Resumen de lo que ya existe en codigo:

- SQLite como fuente de verdad para posiciones, cache y eventos
- `doctor`, `scan`, `inspect`, `paper-open`, `paper-close`, `pre-trade-check`
- backend privado local para credenciales y estado de cuentas
- simulacion paper por patas con maker/taker, delay, drift y fills parciales simulados
- chequeos de liquidez con order books y slippage estimado por venue/lado
- dashboard operativo interactivo con Kill Switch real-time
- adapters de órdenes reales y dependencias (SDKs oficiales) implementados para HL y Lighter (`exchange_adapters.py`)
- flujo de trade `prepare-trade` con cálculos L2 -> `execute-trade` (Market/IOC) con DB tracks
- suite base de tests locales para helpers, señales y endpoints API críticos
- workflow multi-agente y documentación operativa inicial dentro del repo

Lo que sigue faltando para pasar a operaciones 100% reales desatendidas (v0.6+):

- manejo de limit orders para reducir costos Taker
- reconciliacion post-orden contra los balances absolutos del exchange
- manejo de fill parcial real con hedge correctivo inteligente
- alertas operativas si queda exposicion desnuda o ejecución degradada
- replay/backtest simple para validar thresholds sin usar entorno real

## Checklist por version

### Snapshot

- [x] `v0.1` base de research/paper cerrada
- [~] `v0.2` paper realista
- [~] `v0.3` pre-trade engine serio
- [x] `v0.4` panel operativo
- [x] `v0.5` ejecucion asistida
- [ ] `v0.6` primer real money controlado
- [ ] `v0.7` semi-auto
- [ ] `v1.0` operacion autonoma seria

## v0.1

Objetivo: cerrar la base de research y paper.

- mover estado y cache a SQLite
- guardar scans, senales, posiciones paper y eventos en tablas separadas
- agregar logs estructurados por corrida
- health checks reales para HL y Lighter
- comando `doctor` para validar APIs, credenciales y latencia

Entregable:

- bot estable para `scan`, `inspect` y `paper` sin riesgo de corrupcion de estado

Estado:

- [x] estado y cache migrados a SQLite
- [x] scans, posiciones paper y eventos guardados
- [x] `doctor` implementado
- [x] health checks reales para HL y Lighter via backend privado
- [~] logs estructurados por corrida existen como event log, pero no como sistema de observabilidad mas rico

## v0.2

Objetivo: hacer el paper mas realista.

- simular ejecucion por patas
- modelar fills parciales
- modelar delay entre primera y segunda orden
- modelar maker/taker y cancel/repost
- registrar PnL paper con funding + costo + drift de precio

Entregable:

- paper trading que se parezca bastante a una ejecucion real

Estado:

- [x] simulacion por patas
- [x] delay entre patas
- [x] maker/taker y repost simulados
- [x] PnL paper con funding + costo + drift
- [~] fills parciales modelados en simulacion, pero todavia no reconciliados contra venues reales

## v0.3

Objetivo: pre-trade engine serio.

- order book depth check por notional real
- slippage estimado por venue y lado
- score de calidad de oportunidad
- filtros por liquidez minima, edge neto, consistencia y drawdown
- `go / no-go` explicito antes de cualquier entrada

Entregable:

- solo sobreviven oportunidades operables, no solo lindas en pantalla

Estado:

- [x] depth check por notional usando order books
- [x] slippage estimado por venue y lado
- [x] filtros por edge neto, consistencia, drawdown y muestras
- [x] `go / no-go` explicito en `pre-trade-check`
- [~] falta formalizar mejor un score de calidad de oportunidad

## v0.4

Objetivo: panel operativo.

- integrar al HTML estado del bot
- mostrar `fresh/cached`, scan time, latencia y health
- tabla de oportunidades del bot y no solo del monitor
- vista de paper positions, PnL, funding acumulado y alerts
- boton `refresh hard`

Entregable:

- dashboard unico para mirar estrategia y estado operativo

Estado:

- [x] estado del bot integrado al HTML
- [x] tabla de oportunidades del bot
- [x] vista de paper positions y tracking de PnL/funding/drift
- [~] health y latencia visibles, pero todavia falta mas pulido operativo
- [~] acciones y alertas básicas visibles, pero falta una capa más operativa para exposición degradada y reconciliación

## v0.5

Objetivo: ejecucion asistida.

- adapters reales de ordenes para HL y Lighter
- endpoint `pre-trade-check`
- endpoint `prepare-trade`
- endpoint `execute-trade` con confirmacion manual
- limites duros por activo, notional y slippage
- kill switch manual

Entregable:

- vos aprobas, el sistema ejecuta

Estado:

- [x] adapters reales de ordenes para HL y Lighter
- [x] endpoint `prepare-trade`
- [x] endpoint `execute-trade`
- [x] confirmacion manual antes de enviar ordenes reales
- [x] kill switch manual interactivo en dashboard
- [x] limites duros de ejecucion real en CLI/backend

## v0.6

Objetivo: primer real money controlado.

- tamanos minimos
- una sola posicion a la vez
- reconciliacion contra exchange despues de cada orden
- manejo de fill parcial con hedge correctivo
- cierre manual asistido
- alertas si queda exposicion desnuda
- desglose y auditoria clara del costo total ejecutado
- persistencia de fills reales y desvío contra el trade plan

Entregable:

- primeras operaciones reales pequenas con control fuerte

Estado:

- [ ] no iniciado a nivel funcional
- [ ] parcialmente preparado a nivel de testing y observabilidad
- [ ] depende de validar la primera ejecución v0.5 exitosa

### Subfases recomendadas para v0.6

#### v0.6-a Reconciliación post-trade

- guardar fills reales por pata
- consultar estado real de cuenta/posición después de ejecutar
- comparar `trade_plan` vs ejecución real
- persistir desvíos en SQLite

#### v0.6-b Gestión de fill parcial real

- detectar cuándo una pierna queda parcialmente abierta
- marcar severidad de exposición
- intentar hedge correctivo mínimo o dejar instrucción operativa explícita
- registrar resultado del unwind o corrective hedge

#### v0.6-c Alertas operativas

- alertar si `unhedged` supera un umbral
- alertar si `execute_trade` termina en estado degradado
- alertar si un trade queda sin reconciliar
- llevar estos estados al dashboard y al event log

#### v0.6-d Replay / validación offline

- usar históricos ya cacheados para replay básico
- validar thresholds y señales sin tocar entorno real
- usarlo para ajustar `hold_hours`, consistencia mínima y drawdown máximo

## v0.7

Objetivo: semi-auto.

- entrada automatica bajo reglas estrictas
- salida y rebalanceo automaticos
- pausas por volatilidad o APIs degradadas
- limites diarios de perdida
- limites por venue y por activo
- smart execution inicial para reducir costo de entrada

Entregable:

- sistema operativo, pero todavia supervisado

Estado:

- [ ] no iniciado

## v1.0

Objetivo: operacion autonoma seria.

- scheduler continuo
- monitoreo 24/7
- alertas y runbooks
- metricas historicas y reporting
- backtest/replay consistente contra paper/live
- multiples posiciones con motor de riesgo centralizado

Entregable:

- bot realmente deployable

Estado:

- [ ] no iniciado

## Evolución Institucional (Más Allá de v1.0)

Tomando como referencia la arquitectura de Prop-Desks y bots avanzados como Hummingbot o vaults DeFi (ej. Ethena):

- **Smart Execution (Maker-Taker Legging):** En vez de pagar doble taker fee, posicionarse primero pasivo (Limit) del lado más caro. Cuando te toman la orden (fill), ejecutar automáticamente el hedge Taker en la otra plataforma.
- **Auto-Unwind Dinámico:** En vez de salir manualmente, el bot detecta caídas sostenidas de APY o spreads que se inviertan por X horas y liquida ordenadamente la posición antes de comerse pérdidas extra.
- **Auto-Balancing Integrado (Protección a Liquidation):** Monitor de `maintenance_margin`. Si un lado de la posición desangra mucho capital y la otra gana de más, un Daemon mueve USDC (o levanta flags) para evitar "Rekt" del lado perdedor.
- **Micro-predicciones Estructurales (L1/L2):** Añadir un componente de ML o inferencia simple (mirando Open Interest, imbalances de Order Books, o premia) para anticiparse a los picos de funding antes de que un bot ciego de historial lo vea.

## Qué NO es prioridad ahora

Aunque aparezcan en informes de referencia o frameworks externos, no son prioridad inmediata para este repo:

- migrar todo a CCXT
- arbitraje triangular
- arbitraje estadístico/cointregración como estrategia principal
- flash loans / arbitrage DeFi
- reemplazar el dashboard actual por otra UI completa

Todo eso puede ser válido más adelante, pero hoy el cuello de botella real está en ejecución controlada, reconciliación y gestión de riesgo post-trade.

## Prioridad recomendada HOY

1. Cerrar `v0.6-a`: reconciliación post-trade real contra exchange y SQLite.
2. Cerrar `v0.6-b`: manejo de fill parcial real con hedge correctivo o unwind explícito.
3. Cerrar `v0.6-c`: alertas operativas de exposición, ejecución degradada y estados no reconciliados.
4. Agregar `v0.6-d`: replay/backtest simple para validar thresholds sin usar entorno real.
5. Recién después avanzar a `Smart Execution (Maker-Taker Legging)` como puente a `v0.7`.
