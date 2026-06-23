FROM python:3.10-slim-bookworm

ARG SCING_REF=fcea8c5c9a806ee3dbc8123c2d13d1d357137f1d

ENV PYTHONUNBUFFERED=1 \
    SCING_HOME=/opt/SCING \
    SCING_REF=${SCING_REF} \
    CUDA_VISIBLE_DEVICES="" \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    BLIS_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/scing-cache \
    NUMBA_CACHE_DIR=/tmp/numba-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    libgomp1 \
    procps \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip wheel setuptools \
  && python -m pip install --no-cache-dir \
    numpy==1.23.5 \
    pandas==1.5.3 \
    scipy==1.10.1 \
    scikit-learn==1.2.2 \
    matplotlib==3.6.3 \
    seaborn==0.12.2 \
    anndata==0.8.0 \
    scanpy==1.9.1 \
    networkx==2.8.8 \
    dask==2022.10.1 \
    distributed==2022.10.1 \
    bokeh==2.4.3 \
    python-igraph==0.10.8 \
    leidenalg==0.10.2 \
  && python -m pip install --no-cache-dir --no-deps pyitlib==0.2.3

RUN git clone https://github.com/XiaYangLabOrg/SCING.git /opt/SCING \
  && git -C /opt/SCING checkout "${SCING_REF}" \
  && test "$(git -C /opt/SCING rev-parse HEAD)" = "${SCING_REF}" \
  && test -f /opt/SCING/src/scing/buildGRNHelpers.py \
  && test -f /opt/SCING/src/scing/MergeNetworksHelpers.py \
  && rm -rf /opt/SCING/.git

ENV PYTHONPATH="/app:/opt/SCING/src:${PYTHONPATH}"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
