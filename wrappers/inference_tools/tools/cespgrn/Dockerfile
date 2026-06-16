FROM python:3.10-slim-bookworm

ARG CESPGRN_REF=2fb222f8a26edf1bdae14a95b1491543f0aaa4e8

ENV PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
  && python -m pip install --no-cache-dir \
    numpy==1.24.4 \
    pandas==2.0.3 \
    scipy==1.10.1 \
    scikit-learn==1.2.2 \
    networkx==2.8.8 \
    matplotlib==3.7.3 \
  && python -m pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    torch==1.13.1+cpu

RUN git clone https://github.com/PeterZZQ/CeSpGRN.git /opt/CeSpGRN \
  && git -C /opt/CeSpGRN checkout "${CESPGRN_REF}" \
  && test "$(git -C /opt/CeSpGRN rev-parse HEAD)" = "${CESPGRN_REF}" \
  && rm -rf /opt/CeSpGRN/.git

ENV PYTHONPATH="/app:/opt/CeSpGRN/src:${PYTHONPATH}"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY torch_sqrtm.py /app/torch_sqrtm.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
