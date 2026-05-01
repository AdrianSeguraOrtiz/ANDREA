FROM rocker/r-ver:4.4.1

ARG INFERCSN_VERSION=1.2.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    cmake \
    gfortran \
    libcurl4-openssl-dev \
    libfontconfig1-dev \
    libfreetype6-dev \
    libfribidi-dev \
    libharfbuzz-dev \
    libjpeg-dev \
    libpng-dev \
    libssl-dev \
    libtiff-dev \
    libxml2-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN R -q -e "install.packages(c('jsonlite','remotes','RTransferEntropy'), repos='https://cloud.r-project.org')" \
  && R -q -e "remotes::install_version('inferCSN', version='${INFERCSN_VERSION}', repos='https://cloud.r-project.org', dependencies=c('Depends','Imports','LinkingTo'), upgrade='never')" \
  && R -q -e "stopifnot(as.character(utils::packageVersion('inferCSN')) == '${INFERCSN_VERSION}')"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.R /app/run_tool.R

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
