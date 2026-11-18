PORT ?= 8765
PYTHON ?= python3
CADDY ?= caddy

.PHONY: dev caddy-dev test

dev:
	INBOX_TEND_PORT=$(PORT) $(PYTHON) app.py

caddy-dev:
	INBOX_TEND_PORT=$(PORT) $(CADDY) run --config deploy/Caddyfile.local

test:
	$(PYTHON) -m unittest discover -s tests -v
