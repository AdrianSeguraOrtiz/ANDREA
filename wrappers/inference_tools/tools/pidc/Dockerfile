FROM julia:1.10-bookworm

ARG NETWORKINFERENCE_REF=e5a3de323127f002e57bbd91c834f7739939ba0e

ENV DEBIAN_FRONTEND=noninteractive
ENV JULIA_PROJECT=/opt/pidc_env
ENV JULIA_DEPOT_PATH=/tmp/julia_depot:/opt/julia_depot
ENV JULIA_HISTORY=/tmp/julia_history
ENV NETWORKINFERENCE_REF=${NETWORKINFERENCE_REF}
ENV OMP_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV BLIS_NUM_THREADS=1
ENV VECLIB_MAXIMUM_THREADS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/pidc_env /opt/julia_depot /tmp/julia_depot \
    && chmod 1777 /tmp/julia_depot \
    && JULIA_DEPOT_PATH=/opt/julia_depot julia --project=/opt/pidc_env -e '\
        using Pkg; \
        Pkg.add(PackageSpec(url="https://github.com/Tchanders/NetworkInference.jl.git", rev=ENV["NETWORKINFERENCE_REF"])); \
        Pkg.add(PackageSpec(name="JSON", version="0.21.4")); \
    ' \
    && network_file="$(find /opt/julia_depot/packages/NetworkInference -path '*/src/empirical_bayes_glue.jl' -print -quit)" \
    && test -n "${network_file}" \
    && sed -i 's/^EB_EXISTS = .*$/EB_EXISTS = false/' "${network_file}" \
    && JULIA_DEPOT_PATH=/opt/julia_depot julia --project=/opt/pidc_env -e 'using NetworkInference, JSON; using Pkg; Pkg.precompile()' \
    && chmod -R a+rX /opt/pidc_env /opt/julia_depot

COPY run_tool.jl /app/run_tool.jl
COPY run_tool.sh /app/run_tool.sh

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
