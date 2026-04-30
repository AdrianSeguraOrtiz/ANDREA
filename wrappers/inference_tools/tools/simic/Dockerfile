FROM python:3.11-slim-bookworm

WORKDIR /app

ARG SIMIC_REF=fa47e69e5c793bce1500dc35283e112cd8f51992

ENV MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
  && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
  && python -m pip install --no-cache-dir \
    numpy==1.24.4 \
    pandas==1.5.3 \
    scipy==1.10.1 \
    scikit-learn==1.2.2 \
    matplotlib==3.7.5 \
    requests==2.31.0 \
    ipdb==0.13.13 \
  && python -m pip install --no-cache-dir \
    "git+https://github.com/jianhao2016/SimiC.git@${SIMIC_REF}"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
