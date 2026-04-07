# Runbook

## Arranque Basico

### 1. Activar entorno

```bash
cd /home/alan/Documentos/arbitraje
source .venv/bin/activate
```

### 2. Levantar servicios

API publica:

```bash
python funding_arb_server.py
```

Backend privado:

```bash
python private_backend.py
```

### 3. Health checks

```bash
curl http://127.0.0.1:8790/health
curl http://127.0.0.1:8787/health
```

## Comandos Operativos

Doctor:

```bash
python funding_arb_bot.py doctor
```

Scan:

```bash
python funding_arb_bot.py scan --hours 24 --top 15
```

Inspect:

```bash
python funding_arb_bot.py inspect BTC --hours 24
```

Pre-trade:

```bash
python funding_arb_bot.py pre-trade-check BTC --hours 24
```

Smoke:

```bash
python test_smoke.py
```

## Logs a Mirar

- `bot_app.log`
- `.bot_server.log`
- `.private_server.log`
- `logs/bot_errors.ndjson`

## Si Algo Falla

### La API publica no responde

- revisar si `funding_arb_server.py` esta levantado
- mirar `.bot_server.log`
- correr `python funding_arb_bot.py doctor`

### El backend privado falla

- revisar `.env`
- validar `curl http://127.0.0.1:8787/health`
- mirar `.private_server.log`

### Hay errores raros de exchange o 429

- revisar `logs/bot_errors.ndjson`
- revisar `bot_app.log`
- repetir con menos concurrencia y evitando scans repetidos

### El smoke queda inconsistente

- confirmar estado de kill switch:

```bash
curl http://127.0.0.1:8790/api/kill-switch
```

- si quedo mal seteado, restaurarlo explicitamente via API antes de seguir

## Superficie Sensible

No ejecutar sin entender contexto:

- `prepare-trade`
- `execute-trade`
- cualquier cambio en `config.json`
- cualquier cambio en `.env`
