FROM python:3.13-slim AS base
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app

FROM base AS test
RUN pip install --no-cache-dir pytest
COPY tests tests
RUN python -m pytest -q tests

FROM base AS prod
USER 1000:1000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
