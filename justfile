set shell := ["bash", "-cu"]

python := ".venv/bin/python"

setup:
    {{python}} -m pip install -U pip
    {{python}} -m pip install pytest ruff

server:
    {{python}} funding_arb_server.py

private:
    {{python}} private_backend.py

doctor:
    {{python}} funding_arb_bot.py doctor

scan hours="24" top="10":
    {{python}} funding_arb_bot.py scan --hours {{hours}} --top {{top}}

inspect symbol hours="24":
    {{python}} funding_arb_bot.py inspect {{symbol}} --hours {{hours}}

smoke:
    {{python}} test_smoke.py

test:
    {{python}} -m pytest

lint:
    {{python}} -m ruff check .

fmt:
    {{python}} -m ruff format .
