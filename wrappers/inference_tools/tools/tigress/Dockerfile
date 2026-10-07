FROM rocker/r-ver:4.4.1

ARG TIGRESS_REF=de70cd256840b08a64f0471c0c63dbb01314b35a

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    binutils \
    gfortran \
    git \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY patch_upstream.R /tmp/patch_upstream.R
RUN R -q -e "pkgs <- c('jsonlite','doParallel','doRNG','lars','remotes'); install.packages(pkgs, repos='https://cloud.r-project.org'); stopifnot(all(vapply(pkgs, requireNamespace, logical(1), quietly=TRUE)))" \
  && git clone https://github.com/jpvert/tigress.git /tmp/tigress \
  && git -C /tmp/tigress checkout "${TIGRESS_REF}" \
  && test "$(git -C /tmp/tigress rev-parse HEAD)" = "${TIGRESS_REF}" \
  && Rscript /tmp/patch_upstream.R /tmp/tigress \
  && R -q -e "remotes::install_local('/tmp/tigress', upgrade='never')" \
  && rm -rf /tmp/tigress /tmp/patch_upstream.R

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.R /app/run_tool.R

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
