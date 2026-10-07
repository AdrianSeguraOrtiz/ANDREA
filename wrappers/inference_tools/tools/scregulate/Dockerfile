FROM python:3.10-slim-bookworm

ARG SCREGULATE_PRIOR_REF=ed32e9083f9ebaf391ab4f2aba48374a4c5d0f5b

ENV PYTHONUNBUFFERED=1 \
    CUDA_VISIBLE_DEVICES="" \
    SCREGULATE_PRIORS_DIR=/opt/scregulate_priors \
    SCREGULATE_PRIOR_REF=${SCREGULATE_PRIOR_REF} \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    BLIS_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/scregulate-cache \
    NUMBA_CACHE_DIR=/tmp/numba-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgomp1 \
    procps \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/matplotlib /tmp/scregulate-cache /tmp/numba-cache \
  && chmod 1777 /tmp/matplotlib /tmp/scregulate-cache /tmp/numba-cache

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip wheel setuptools \
  && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.2.2+cpu \
  && python -m pip install --no-cache-dir \
    numpy==1.26.4 \
    pandas==2.2.2 \
    scipy==1.13.1 \
    scikit-learn==1.5.2 \
    anndata==0.10.9 \
    scanpy==1.10.4 \
    optuna==4.4.0 \
    scRegulate==1.0.1

RUN mkdir -p "${SCREGULATE_PRIORS_DIR}" \
  && python - <<'PY'
from pathlib import Path
import hashlib
import os
import urllib.request

dst = Path("/opt/scregulate_priors")
dst.mkdir(parents=True, exist_ok=True)
checksums = {
    "collectri_human_net.csv": "5313506f33f38c7d7ed402292feec435a75b9849c982d890b75e530a44b18b8e",
    "collectri_mouse_net.csv": "b5714c231abb9de06a343d4be531c20ce0f4d0a9c7e88ce0a473ea2aab15c9a8",
}
for filename, expected in checksums.items():
    url = f"https://raw.githubusercontent.com/YDaiLab/scRegulate/{os.environ['SCREGULATE_PRIOR_REF']}/priors/{filename}"
    data = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Unexpected CollecTRI content: {filename}")
    (dst / filename).write_bytes(data)
PY

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
