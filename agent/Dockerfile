FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install "strands-agents[litellm]"

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 agent
COPY --from=build /install /usr/local
COPY agent/oncall.py /app/oncall.py
USER 10001
ENTRYPOINT ["python", "/app/oncall.py"]