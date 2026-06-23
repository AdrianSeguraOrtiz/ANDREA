# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm

ARG GNW_REPO=https://github.com/tschaffter/genenetweaver.git
ARG GNW_COMMIT=c5310349f5d5723306585c2bb62aedbdeb70db46

ENV DEBIAN_FRONTEND=noninteractive \
    GNW_HOME=/opt/genenetweaver \
    GNW_JAR=/opt/genenetweaver/gnw-3.1.2b.jar \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN git clone "${GNW_REPO}" "${GNW_HOME}" \
    && git -C "${GNW_HOME}" checkout "${GNW_COMMIT}" \
    && test "$(git -C "${GNW_HOME}" rev-parse HEAD)" = "${GNW_COMMIT}" \
    && test -f "${GNW_JAR}" \
    && test -f "${GNW_HOME}/sandbox/InSilicoSize10-Yeast1.xml"

WORKDIR /app

COPY wrappers/simulation_data_tools/simulators/genenetweaver/run_simulator.py /app/run_simulator.py

RUN chmod +x /app/run_simulator.py \
    && python -m py_compile /app/run_simulator.py

ENTRYPOINT ["python", "/app/run_simulator.py", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
