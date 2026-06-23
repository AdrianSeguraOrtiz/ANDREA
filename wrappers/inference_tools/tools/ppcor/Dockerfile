FROM rocker/r-ver:4.4.1

WORKDIR /app

ARG PPCOR_VERSION=1.1
ARG PPCOR_TARBALL=https://cran.r-project.org/src/contrib/ppcor_1.1.tar.gz

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

RUN R -q -e "install.packages('jsonlite', repos='https://cloud.r-project.org')" \
  && R -q -e "install.packages('${PPCOR_TARBALL}', repos=NULL, type='source')" \
  && R -q -e "stopifnot(as.character(utils::packageVersion('ppcor')) == '${PPCOR_VERSION}')"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.R /app/run_tool.R

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
