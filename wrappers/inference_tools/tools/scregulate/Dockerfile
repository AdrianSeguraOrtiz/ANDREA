FROM python:3.10-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    CUDA_VISIBLE_DEVICES="" \
    SCREGULATE_PRIORS_DIR=/opt/scregulate_priors \
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
import shutil

from scregulate.datasets import collectri_prior

for species in ("human", "mouse"):
    collectri_prior(species)

src = Path.home() / ".scregulate" / "priors"
dst = Path("/opt/scregulate_priors")
dst.mkdir(parents=True, exist_ok=True)
for filename in ("collectri_human_net.csv", "collectri_mouse_net.csv"):
    source = src / filename
    if not source.exists():
        raise FileNotFoundError(source)
    shutil.copy2(source, dst / filename)
PY

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
