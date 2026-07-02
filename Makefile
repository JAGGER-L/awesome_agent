.PHONY: check install setup-sandbox dev docker-init docker-start docker-stop test

check:
	uv run python scripts/make/check.py

install:
	uv run python scripts/make/install.py

setup-sandbox:
	uv run python scripts/make/setup_sandbox.py

dev:
	uv run python scripts/make/dev.py

docker-init:
	uv run python scripts/make/docker_init.py

docker-start:
	uv run python scripts/make/docker_start.py

docker-stop:
	docker compose down

test:
	pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
