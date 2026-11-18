PY ?= .venv/bin/python
BIN = .venv/bin

.PHONY: install lint format test e2e screenshots demo run docker smoke
install:
	python3 -m venv .venv && $(BIN)/pip install -e ".[dev]"
lint:
	$(PY) tools/check_js.py
	$(BIN)/ruff check . && $(BIN)/ruff format --check .
format:
	$(BIN)/ruff format . && $(BIN)/ruff check --fix .
test:
	$(BIN)/pytest -m "not e2e" --cov --cov-report=term-missing
e2e:
	$(BIN)/pytest -m e2e tests/e2e
screenshots:
	$(PY) tools/screenshots.py
demo:
	$(PY) -m switchyard --data /tmp/switchyard-demo serve --demo
run:
	$(PY) -m switchyard serve
docker:
	docker build -t switchyard:dev .
smoke:
	bash tools/smoke.sh
