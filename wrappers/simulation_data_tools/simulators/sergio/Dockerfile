FROM python:3.10-slim

ARG SERGIO_REPO=https://github.com/PayamDiba/SERGIO.git
ARG SERGIO_COMMIT=a6190b74425112834c8fa9b4b6157d9cb3d1ab88

ENV DEBIAN_FRONTEND=noninteractive \
    SERGIO_HOME=/opt/SERGIO \
    PYTHONPATH=/opt/SERGIO

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
        numpy==1.23.5 \
        scipy==1.10.1 \
        networkx==2.8.8

RUN git clone "${SERGIO_REPO}" "${SERGIO_HOME}" \
    && git -C "${SERGIO_HOME}" checkout "${SERGIO_COMMIT}" \
    && test "$(git -C "${SERGIO_HOME}" rev-parse HEAD)" = "${SERGIO_COMMIT}"

WORKDIR /app

COPY wrappers/simulation_data_tools/simulators/sergio/run_simulator.py /app/run_simulator.py

RUN chmod +x /app/run_simulator.py \
    && python -c "import SERGIO.sergio; print('SERGIO import ok')"

ENTRYPOINT ["python", "/app/run_simulator.py", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
