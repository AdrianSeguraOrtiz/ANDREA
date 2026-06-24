FROM python:3.8-slim-bullseye

ARG SCSGL_REF=7fb2a011f6e1061daf4c976225027e76f4e0e4ea

ENV PYTHONUNBUFFERED=1 \
    SCSGL_HOME=/opt/scSGL \
    SCSGL_REF=${SCSGL_REF} \
    CUDA_VISIBLE_DEVICES="" \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    BLIS_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    NUMBA_NUM_THREADS=1 \
    RCPP_PARALLEL_NUM_THREADS=1 \
    R_LIBS_SITE=/usr/local/lib/R/site-library \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/scsgl-cache \
    NUMBA_CACHE_DIR=/tmp/numba-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    build-essential \
    gfortran \
    libgomp1 \
    libblas-dev \
    liblapack-dev \
    r-base-core \
    r-base-dev \
    procps \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/matplotlib /tmp/scsgl-cache /tmp/numba-cache \
  && chmod 1777 /tmp/matplotlib /tmp/scsgl-cache /tmp/numba-cache

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip==24.0 setuptools==69.5.1 wheel==0.43.0 \
  && python -m pip install --no-cache-dir \
    numpy==1.20.2 \
    scipy==1.6.3 \
    pandas==1.2.4 \
    scikit-learn==0.24.1 \
    numba==0.53.1 \
    rpy2==3.4.4

RUN R -q -e "install.packages('pcaPP', repos='https://cloud.r-project.org')"

RUN git clone https://github.com/Single-Cell-Graph-Learning/scSGL.git /opt/scSGL \
  && git -C /opt/scSGL checkout "${SCSGL_REF}" \
  && test "$(git -C /opt/scSGL rev-parse HEAD)" = "${SCSGL_REF}" \
  && test -f /opt/scSGL/pysrc/graphlearning/__init__.py \
  && rm -rf /opt/scSGL/.git

ENV PYTHONPATH="/app:/opt/scSGL:${PYTHONPATH}"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
