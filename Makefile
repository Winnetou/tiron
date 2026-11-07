IMAGE ?= tiron
PORT ?= 8000
ENV_FILE ?= .env
BACKEND_URL ?= http://localhost:$(PORT)

.PHONY: build run frontend

build:
	docker build -t $(IMAGE) .

# Build the image and run the container. The POSTGRES_* variables are read
# from $(ENV_FILE) (see .env.example).
run: build
	docker run --rm --init --name $(IMAGE) -p $(PORT):8000 --env-file $(ENV_FILE) $(IMAGE)

# Run the frontend dev server (http://localhost:5173), proxying API calls to
# the backend started by `make run`.
frontend:
	cd frontend && npm install && BACKEND_URL=$(BACKEND_URL) npm run dev
