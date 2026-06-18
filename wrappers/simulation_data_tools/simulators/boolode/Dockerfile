FROM python:3.7-slim-bullseye

ARG BOOLODE_REPO=https://github.com/Murali-group/BoolODE.git
ARG BOOLODE_COMMIT=ba8884af40f98fc648b3f36f0b81a5a8cf22c9b9

ENV DEBIAN_FRONTEND=noninteractive \
    BOOLODE_HOME=/opt/BoolODE \
    PYTHONPATH=/opt/BoolODE \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade "pip<24" setuptools wheel

RUN git clone "${BOOLODE_REPO}" "${BOOLODE_HOME}" \
    && git -C "${BOOLODE_HOME}" checkout "${BOOLODE_COMMIT}" \
    && test "$(git -C "${BOOLODE_HOME}" rev-parse HEAD)" = "${BOOLODE_COMMIT}"

RUN python -m pip install --no-cache-dir -r "${BOOLODE_HOME}/requirements.txt" \
    && python -m pip install --no-cache-dir PyYAML==6.0.1

WORKDIR /app

COPY wrappers/simulation_data_tools/simulators/boolode/run_simulator.py /app/run_simulator.py

RUN chmod +x /app/run_simulator.py \
    && python -c "import BoolODE; print('BoolODE import ok')"

ENTRYPOINT ["python", "/app/run_simulator.py", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
