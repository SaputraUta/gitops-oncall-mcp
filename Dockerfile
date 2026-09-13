FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 mcp
COPY --from=build /install /usr/local
USER mcp
EXPOSE 8765
ENV MCP_HOST=0.0.0.0 MCP_PORT=8765
CMD ["gitops-oncall-mcp"]