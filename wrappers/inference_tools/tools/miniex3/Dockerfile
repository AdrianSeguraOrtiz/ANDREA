FROM psbdock/mini-ex:v3.2

ARG MINIEX_REF=3f220a68e8057fe4d33665e956a3f3bbe41ff4c8
ARG NEXTFLOW_VERSION=24.10.5

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        default-jre-headless \
        file \
        git \
        procps \
        python3 \
    && rm -rf /var/lib/apt/lists/*

ENV NXF_VER=${NEXTFLOW_VERSION}

RUN curl -fsSL https://get.nextflow.io | bash \
    && mv nextflow /usr/local/bin/nextflow \
    && chmod +x /usr/local/bin/nextflow \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

RUN git init /opt/MINI-EX \
    && git -C /opt/MINI-EX remote add origin https://github.com/VIB-PSB/MINI-EX.git \
    && git -C /opt/MINI-EX fetch --depth 1 origin "${MINIEX_REF}" \
    && git -C /opt/MINI-EX checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/MINI-EX rev-parse HEAD)" = "${MINIEX_REF}" \
    && chmod -R a+rX /opt/MINI-EX

# MINI-EX's Nextflow graph uses root-level dummy paths for nullable inputs.
RUN touch \
        /.dummy_path_terms_of_interest \
        /.dummy_path_grnboost \
        /.dummy_path_motif_mapping \
        /.dummy_path_info_tf \
        /.dummy_path_go_annotations \
        /.dummy_path_gene_aliases \
        /.dummy_path_enrichment_background \
        /.dummy_path_go_enrichment \
    && chmod a+r \
        /.dummy_path_terms_of_interest \
        /.dummy_path_grnboost \
        /.dummy_path_motif_mapping \
        /.dummy_path_info_tf \
        /.dummy_path_go_annotations \
        /.dummy_path_gene_aliases \
        /.dummy_path_enrichment_background \
        /.dummy_path_go_enrichment

ENV PATH="/usr/local/bin:/opt/conda/bin:/usr/bin:/bin:/usr/local/games:/usr/games" \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
