PYTHON ?= python3

.PHONY: rebuild check
rebuild:
	PYTHONPATH=src $(PYTHON) -m transit_safety_lens.pipeline --root .

check:
	$(PYTHON) -m ruff check src tests scripts
	$(PYTHON) -m mypy src
	$(PYTHON) -m pytest
