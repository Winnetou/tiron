IMAGE ?= tiron
PORT ?= 8000
ENV_FILE ?= .env
BACKEND_URL ?= http://localhost:$(PORT)

.PHONY: build run frontend up down

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
into_db:
	docker exec -it  tiron-db-1 psql -Uu tiron
	
into_shell:
	docker exec -it tiron-backend-1  /bin/bash
restart:
	docker compose restart backend
