PORT ?= 8765
PYTHON ?= python3
CADDY ?= caddy

.PHONY: dev caddy-dev test build-pages

dev:
	INBOX_TEND_PORT=$(PORT) $(PYTHON) app.py

caddy-dev:
	INBOX_TEND_PORT=$(PORT) $(CADDY) run --config deploy/Caddyfile.local

test:
	$(PYTHON) -m unittest discover -s tests -v

build-pages:
	$(PYTHON) scripts/build_pages.py
