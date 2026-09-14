FROM python:3.12-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir --prefix=/install "strands-agents[litellm]"

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 agent
COPY --from=build /install /usr/local
WORKDIR /app
COPY agent ./agent
USER 10001
# Long-running by default: the conversation must outlive a single turn, so a
# proposal made now is still live when the human replies minutes later.
# run.sh overrides the command for a one-shot triage.
ENTRYPOINT ["python"]
CMD ["-m", "agent.server"]
