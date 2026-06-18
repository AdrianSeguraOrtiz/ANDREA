FROM rocker/r2u:24.04

ARG SCRENI_REF=695772f8555f0b22df1c331e3c900075fee03b2c

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    r-bioc-genie3 \
    r-cran-doparallel \
    r-cran-foreach \
    r-cran-jsonlite \
    r-cran-remotes \
    r-cran-seurat \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN R -q -e "remotes::install_github('Xuxl2020/ScReNI', ref='${SCRENI_REF}', dependencies=FALSE, upgrade='never')" \
  && R -q -e "stopifnot(as.character(utils::packageVersion('ScReNI')) == '1.0.0')"

RUN sed -i '/options(bspm.version.check=FALSE)/a options(bspm.sudo = TRUE)' /usr/lib/R/etc/Rprofile.site

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.R /app/run_tool.R

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
