FROM python:3.11-slim-bookworm

ARG SCGENERAI_REF=77177a2589775ff125622bc4e8bf26a54f95ca62

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir \
    numpy==1.26.4 \
    pandas==2.2.2 \
    tqdm==4.66.4 \
  && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.3.1

RUN git clone https://github.com/PhGK/scGeneRAI.git /opt/scGeneRAI \
  && git -C /opt/scGeneRAI checkout "${SCGENERAI_REF}" \
  && rm -rf /opt/scGeneRAI/.git

ENV PYTHONPATH="/opt/scGeneRAI:${PYTHONPATH}"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
