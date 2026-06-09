# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Default — SSE transport on :9234 for the QNAP alfacore-caddy network.
# Override PRODUCTGRAPH_MCP_TRANSPORT=stdio to run as a Claude Desktop subprocess.
ENV PRODUCTGRAPH_MCP_TRANSPORT=sse \
    PRODUCTGRAPH_MCP_HOST=0.0.0.0 \
    PRODUCTGRAPH_MCP_PORT=9234

EXPOSE 9234

ENTRYPOINT ["product-graph-mcp"]
