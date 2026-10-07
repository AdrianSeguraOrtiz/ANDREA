FROM rocker/r2u:24.04

ARG DYNGEN_VERSION=1.1.1
ARG DYNGEN_DATA_COMMIT=767f1d11fb7fe3706e0431f6197818c3ad50d359
ARG DYNGEN_DATA_SHA256=7a7e813008330c4ba822278b4e0390bf85dd35169f3e7e2fa4e400159cd5ac9b
ENV DEBIAN_FRONTEND=noninteractive \
    R_CRAN_MIRROR=https://cloud.r-project.org \
    DYNGEN_CACHE_DIR=/opt/dyngen-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libgit2-dev \
        libglpk-dev \
        r-cran-jsonlite \
        r-cran-remotes \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN R -q -e "options(bspm.sudo = TRUE); install.packages(c('assertthat', 'dplyr', 'dynutils', 'ggplot2', 'ggraph', 'GillespieSSA2', 'igraph', 'lmds', 'Matrix', 'patchwork', 'pbapply', 'purrr', 'rlang', 'tibble', 'tidygraph', 'tidyr', 'tidyselect', 'viridis'), repos = Sys.getenv('R_CRAN_MIRROR'))" \
    && R -q -e "options(bspm.sudo = TRUE); remotes::install_version('dyngen', version = Sys.getenv('DYNGEN_VERSION'), repos = Sys.getenv('R_CRAN_MIRROR'), upgrade = 'never', dependencies = FALSE)" \
    && R -q -e "stopifnot(as.character(utils::packageVersion('dyngen')) == Sys.getenv('DYNGEN_VERSION'))"

RUN mkdir -p "$DYNGEN_CACHE_DIR" \
    && curl -fsSL "https://codeload.github.com/dynverse/dyngen/zip/${DYNGEN_DATA_COMMIT}" -o /tmp/dyngen-data.zip \
    && echo "${DYNGEN_DATA_SHA256}  /tmp/dyngen-data.zip" | sha256sum -c - \
    && unzip -q -j /tmp/dyngen-data.zip -d "$DYNGEN_CACHE_DIR" \
    && rm /tmp/dyngen-data.zip

WORKDIR /app

COPY wrappers/simulation_data_tools/simulators/dyngen/run_simulator.R /app/run_simulator.R

RUN chmod +x /app/run_simulator.R

ENTRYPOINT ["Rscript", "/app/run_simulator.R", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
