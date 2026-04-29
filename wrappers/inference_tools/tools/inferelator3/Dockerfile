FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV MPLCONFIGDIR=/tmp/matplotlib

RUN python -m pip install --no-cache-dir --upgrade pip \
  && python -m pip install --no-cache-dir \
    "numpy<2" \
    inferelator==0.6.3 \
    joblib==1.5.3

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
