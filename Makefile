PORT ?= 8765
PYTHON ?= python3
CADDY ?= caddy

.PHONY: dev caddy-dev test build-pages css css-watch css-check vendor

dev:
	INBOX_TEND_PORT=$(PORT) $(PYTHON) app.py

caddy-dev:
	INBOX_TEND_PORT=$(PORT) $(CADDY) run --config deploy/Caddyfile.local

test:
	$(PYTHON) -m unittest discover -s tests -v

build-pages:
	$(PYTHON) scripts/build_pages.py

# Front end. Only needed when you change the look: the compiled CSS and Font Awesome files are committed,
# so running the app needs no Node. First time: npm ci
css:
	npm run build:css
	$(PYTHON) scripts/build_pages.py

css-watch:
	npm run watch:css

css-check: css
	git diff --exit-code static/styles.css

vendor:
	npm run vendor
