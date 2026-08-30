PY ?= ./.venv/bin/python
PIP ?= ./.venv/bin/pip

.PHONY: install dev serve desktop demo doctor test lint geo clean

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

serve:
	$(PY) -m leadgen serve

desktop:
	$(PY) -m leadgen serve --desktop

demo:
	$(PY) -m leadgen demo

doctor:
	$(PY) -m leadgen doctor

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m pytest -q
	$(PY) -m compileall -q leadgen scripts

geo:
	$(PY) scripts/build_geo.py

clean:
	rm -rf .pytest_cache leadgen/__pycache__ leadgen/**/__pycache__
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
