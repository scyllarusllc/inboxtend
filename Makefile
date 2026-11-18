PORT ?= 8765
PYTHON ?= python3

.PHONY: dev test

dev:
	INBOX_TEND_PORT=$(PORT) $(PYTHON) app.py

test:
	$(PYTHON) -m unittest discover -s tests -v
