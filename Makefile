IMAGE ?= tiron
PORT ?= 8000
ENV_FILE ?= .env
BACKEND_URL ?= http://localhost:$(PORT)

.PHONY: build run frontend up down prod-up prod-down prod-config external-edition

build:
	docker build -t $(IMAGE) .

# Build the image and run the container. The POSTGRES_* variables are read
# from $(ENV_FILE) (see .env.example).
run: build
	docker run --rm --init --name $(IMAGE) -p $(PORT):8000 --env-file $(ENV_FILE) $(IMAGE)

# Run the frontend dev server on the host (http://localhost:5173), proxying API
# calls to the backend started by `make run`. `make up` runs it in a container.
frontend:
	cd frontend && npm install && BACKEND_URL=$(BACKEND_URL) npm run dev

# The backend, a PostgreSQL database with a volume, and the frontend, together
# (see docker-compose.yml). `down` stops them and keeps the data in the volume.
up:
	docker compose up --build

down:
	docker compose down

# Production (docker-compose.prod.yml, settings in .env.prod): no database of its own
# (it is in Google Cloud), the built frontend behind Caddy. `prod-config` prints what
# compose makes of it, and fails if a setting that has no default is missing.
PROD_COMPOSE = docker compose -f docker-compose.prod.yml --env-file .env.prod

prod-up:
	$(PROD_COMPOSE) up -d --build

prod-down:
	$(PROD_COMPOSE) down

prod-config:
	$(PROD_COMPOSE) config
into_db:
	docker exec -it  tiron-db-1 psql -Uu tiron
	
into_shell:
	docker exec -it tiron-backend-1  /bin/bash
restart:
	docker compose restart backend

# Import an external edition (a ready text of wolnelektury.pl) into the database of the
# POSTGRES_* variables of $(ENV_FILE) (see app/external_editions/cli.py). It asks for
# the link, the author, the title and the year.
external-edition:
	set -a && . ./$(ENV_FILE) && set +a && uv run python -m app.external_editions
