FROM python:3.13-alpine AS base
# wg (handshake, endpoint), ss (per-player sockets), ping (tunnel latency)
RUN apk add --no-cache wireguard-tools-wg iproute2-ss iputils-ping
WORKDIR /srv
COPY netmon.py .

FROM base AS test
RUN pip install --no-cache-dir pytest
COPY test_netmon.py .
RUN python -m pytest -q

FROM base AS prod
CMD ["python", "-u", "netmon.py", "host"]
