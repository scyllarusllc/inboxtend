PORT ?= 8765
NEXT_PORT ?= 3000
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
CADDY ?= caddy

.PHONY: deploy dev dev-api dev-next caddy-dev test build-pages css css-watch css-check vendor demo

dev:
	python3 scripts/bootstrap_dev.py
	.venv/bin/python scripts/free_dev_port.py $(PORT)
	INBOX_TEND_DEV_RELOAD=1 INBOX_TEND_PORT=$(PORT) .venv/bin/python cli.py serve --port $(PORT) --reload

dev-api:
	python3 scripts/bootstrap_dev.py
	.venv/bin/python scripts/free_dev_port.py $(PORT)
	INBOX_TEND_PORT=$(PORT) .venv/bin/python cli.py serve --port $(PORT)

dev-next:
	python3 scripts/bootstrap_dev.py
	test -d node_modules/next || npm ci
	.venv/bin/python scripts/free_dev_port.py $(PORT)
	.venv/bin/python scripts/free_dev_port.py $(NEXT_PORT)
	INBOX_TEND_PORT=$(PORT) .venv/bin/python cli.py serve --port $(PORT) & backend_pid=$$!; \
	trap 'kill $$backend_pid 2>/dev/null || true' EXIT INT TERM; \
	INBOX_TEND_API_ORIGIN=http://127.0.0.1:$(PORT) npm run next:dev -- --port $(NEXT_PORT)

caddy-dev:
	INBOX_TEND_PORT=$(PORT) $(CADDY) run --config deploy/Caddyfile.local

test:
	$(PYTHON) -m unittest discover -s tests -v

build-pages:
	$(PYTHON) scripts/build_pages.py

# Front end stylesheet and vendor assets.
css:
	npm run build:css
	$(PYTHON) scripts/build_pages.py

css-watch:
	npm run watch:css

css-check: css
	git diff --exit-code static/styles.css

vendor:
	npm run vendor

# Try the app with fictional data and no Gmail: http://127.0.0.1:8781, sign in as demo@example.org / demo-password-123
demo:
	$(PYTHON) scripts/demo_data.py /tmp/inboxtend-demo/tasks.sqlite3
	INBOX_TEND_DB=/tmp/inboxtend-demo/tasks.sqlite3 INBOX_TEND_PORT=8781 $(PYTHON) app.py

deploy:
	scripts/deploy.sh
