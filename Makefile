.PHONY: init up check down
init:
	docker run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/work" -w /work python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea python scripts/init-secrets.py
up:
	docker compose up --build -d --wait
check:
	docker compose --profile test build tests orchestrator-tests
	docker compose --profile test run --rm tests
	docker compose --profile test run --rm orchestrator-tests
	docker compose config --format json | docker run --rm -i -v "$(CURDIR)/scripts:/scripts:ro" python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea python -I /scripts/check-boundaries.py
	docker compose run --rm --no-deps backend ruff check app tests migrations
	docker compose run --rm --no-deps backend mypy app tests migrations
down:
	docker compose down

.PHONY: backup
backup:
	sh scripts/backup.sh

.PHONY: test-recovery
test-recovery:
	sh scripts/test-recovery.sh
