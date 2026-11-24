# The frontend's dev server (Vite), for docker compose: it serves the app on
# port 5173 and proxies the backend's routes to BACKEND_URL (see vite.config.ts).
# The sources are mounted over /app by docker-compose.yml, so edits show at once;
# the dependencies stay the ones installed here.
FROM node:25-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY . .

EXPOSE 5173
# 0.0.0.0: reachable from outside the container (the default is localhost only).
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]
