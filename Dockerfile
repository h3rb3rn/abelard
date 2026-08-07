FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir poetry==1.8.5

WORKDIR /app

COPY pyproject.toml ./
RUN if [ -f pyproject.toml ]; then \
      poetry config virtualenvs.create false && \
      poetry install --no-interaction --no-ansi; \
    fi




COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "1800"]
