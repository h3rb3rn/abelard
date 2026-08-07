FROM python:3.12-slim as base

RUN pip install poetry==1.8.5

WORKDIR /app

COPY pyproject.toml ./
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi

CMD ["sh", "-c", "poetry update; pytest -v"]
