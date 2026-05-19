FROM bioconductor/bioconductor_docker:RELEASE_3_23

ARG LIONESSR_VERSION=1.26.0

RUN R -q -e "BiocManager::install('lionessR', ask=FALSE, update=FALSE)" \
  && R -q -e "stopifnot(as.character(utils::packageVersion('lionessR')) == '${LIONESSR_VERSION}')"

WORKDIR /app

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.R /app/run_tool.R

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
