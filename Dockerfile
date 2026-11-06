FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/0.7.22/install.sh | sh

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:/root/.local/bin:$PATH"

WORKDIR /app

# Install dependencies first for better layer caching
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# spaCy Polish pipeline (same model `python -m spacy download pl_core_news_lg`
# fetches). The uv venv has no pip, which `spacy download` needs, so install
# the official wheel directly. Keep the version in the 3.8.x line to match spacy.
ARG SPACY_PL_MODEL_VERSION=3.8.0
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install \
    "https://github.com/explosion/spacy-models/releases/download/pl_core_news_lg-${SPACY_PL_MODEL_VERSION}/pl_core_news_lg-${SPACY_PL_MODEL_VERSION}-py3-none-any.whl"

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --inexact

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
