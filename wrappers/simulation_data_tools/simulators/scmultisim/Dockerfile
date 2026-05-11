FROM bioconductor/bioconductor_docker:RELEASE_3_23

ARG SCMULTISIM_VERSION=1.8.0
ENV DEBIAN_FRONTEND=noninteractive \
    SCMULTISIM_VERSION=${SCMULTISIM_VERSION}

RUN R -q -e "options(Ncpus = max(1L, parallel::detectCores())); BiocManager::install(c('scMultiSim', 'jsonlite'), version = '3.23', ask = FALSE, update = FALSE); stopifnot(as.character(utils::packageVersion('scMultiSim')) == Sys.getenv('SCMULTISIM_VERSION'))"

WORKDIR /app

COPY wrappers/simulation_data_tools/simulators/scmultisim/run_simulator.R /app/run_simulator.R

RUN chmod +x /app/run_simulator.R

ENTRYPOINT ["Rscript", "/app/run_simulator.R", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
