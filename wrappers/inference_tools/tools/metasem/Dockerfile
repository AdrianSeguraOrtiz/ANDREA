FROM python:3.10-slim-bookworm

ARG METASEM_REF=482987b360ca57172f0276fd64e27b2681223b00

ENV PYTHONUNBUFFERED=1 \
    METASEM_HOME=/opt/MetaSEM \
    METASEM_REF=${METASEM_REF} \
    CUDA_VISIBLE_DEVICES="" \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    BLIS_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip wheel \
  && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.0.0+cpu \
  && python -m pip install --no-cache-dir \
    numpy==1.24.4 \
    pandas==1.5.3

RUN git clone https://github.com/ZhangLab312/MetaSEM.git /opt/MetaSEM \
  && git -C /opt/MetaSEM checkout "${METASEM_REF}" \
  && test "$(git -C /opt/MetaSEM rev-parse HEAD)" = "${METASEM_REF}"

COPY patched_src/MetaSEM_Model.py /opt/MetaSEM/SRC/MetaSEM_Model.py
COPY patched_src/MetaSEM_tool.py /opt/MetaSEM/SRC/MetaSEM_tool.py
COPY patched_src/MetaSEM_Train_GRN_inference.py /opt/MetaSEM/SRC/MetaSEM_Train_GRN_inference.py

RUN rm -rf /opt/MetaSEM/.git

ENV PYTHONPATH="/app:/opt/MetaSEM:${PYTHONPATH}"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
