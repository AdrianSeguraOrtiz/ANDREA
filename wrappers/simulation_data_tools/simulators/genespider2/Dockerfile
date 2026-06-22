# syntax=docker/dockerfile:1.7
FROM ubuntu:24.04

ARG GENESPIDER2_REPO=https://bitbucket.org/sonnhammergrni/genespider.git
ARG GENESPIDER2_COMMIT=0ac785abf89dbf65cb01132da703d0e75196abc2
ARG MATLAB_RUNTIME_URL=https://ssd.mathworks.com/supportfiles/downloads/R2026a/Release/3/deployment_files/installer/complete/glnxa64/MATLAB_Runtime_R2026a_Update_3_glnxa64.zip
ARG MATLAB_RUNTIME_ROOT=/opt/matlab_runtime/R2026a
ARG ANDREA_USER=adrian
ARG ANDREA_UID=1001
ARG ANDREA_GID=1001

ENV DEBIAN_FRONTEND=noninteractive \
    GENESPIDER2_HOME=/opt/genespider \
    GENESPIDER2_COMPILED_RUNNER=/opt/genespider2/compiled/run_run_genespider2.sh \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    MATLAB_RUNTIME_ROOT=${MATLAB_RUNTIME_ROOT} \
    MCR_CACHE_ROOT=/tmp/mcr_cache \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-tools \
        libasound2t64 \
        libatomic1 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libc6 \
        libcairo-gobject2 \
        libcairo2 \
        libcap2 \
        libcups2t64 \
        libdbus-1-3 \
        libdrm2 \
        libfontconfig1 \
        libfreetype6 \
        libfribidi0 \
        libgbm1 \
        libgdk-pixbuf-2.0-0 \
        libgl1 \
        libglib2.0-0t64 \
        libgstreamer-plugins-base1.0-0 \
        libgstreamer1.0-0 \
        libgtk-3-0 \
        libgtk-3-0t64 \
        libgtk2.0-0t64 \
        libice6 \
        libltdl7 \
        libnettle8t64 \
        libnspr4 \
        libnss3 \
        libopengl0 \
        libpam0g \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        libpixman-1-0 \
        libsndfile1 \
        libtirpc3t64 \
        libuuid1 \
        libwayland-client0 \
        libx11-6 \
        libx11-xcb1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxfont2 \
        libxft2 \
        libxi6 \
        libxinerama1 \
        libxmu6 \
        libxrandr2 \
        libxrender1 \
        libxss1 \
        libxt6t64 \
        libxtst6 \
        libxxf86vm1 \
        locales \
        net-tools \
        procps \
        python3 \
        python3-numpy \
        unzip \
        x11-xkb-utils \
        wget \
        zlib1g \
    && locale-gen en_US.UTF-8 \
    && [ -d /usr/share/X11/xkb ] || mkdir -p /usr/share/X11/xkb \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/tmp/matlab_runtime_cache \
    set -eux; \
    wget --tries=5 --waitretry=10 --continue --progress=dot:giga \
        -O /tmp/matlab_runtime_cache/matlab_runtime.zip "${MATLAB_RUNTIME_URL}"; \
    mkdir -p /tmp/matlab_runtime_installer /opt/matlab_runtime; \
    unzip -q /tmp/matlab_runtime_cache/matlab_runtime.zip -d /tmp/matlab_runtime_installer; \
    printf "destinationFolder=/opt/matlab_runtime\nagreeToLicense=yes\n" > /tmp/matlab_runtime_installer/installInputs.txt; \
    cd /tmp/matlab_runtime_installer; \
    ./install -bat true -inputFile ./installInputs.txt -outputFile /tmp/matlab_runtime_install.log \
        || { status="$?"; cat /tmp/matlab_runtime_install.log || true; exit "$status"; }; \
    if [ -e "${MATLAB_RUNTIME_ROOT}/sys/os/glnxa64/libstdc++.so.6" ]; then \
        rm -f "${MATLAB_RUNTIME_ROOT}/sys/os/glnxa64/libstdc++.so.6"; \
        ln -s /usr/lib/x86_64-linux-gnu/libstdc++.so.6 "${MATLAB_RUNTIME_ROOT}/sys/os/glnxa64/libstdc++.so.6"; \
    fi; \
    rm -rf /tmp/matlab_runtime_installer /tmp/matlab_runtime_install.log

RUN git clone "${GENESPIDER2_REPO}" "${GENESPIDER2_HOME}" \
    && git -C "${GENESPIDER2_HOME}" checkout "${GENESPIDER2_COMMIT}" \
    && test "$(git -C "${GENESPIDER2_HOME}" rev-parse HEAD)" = "${GENESPIDER2_COMMIT}"

RUN if ! getent group "${ANDREA_GID}" >/dev/null; then groupadd --gid "${ANDREA_GID}" "${ANDREA_USER}"; fi \
    && if ! id -u "${ANDREA_USER}" >/dev/null 2>&1; then useradd --create-home --uid "${ANDREA_UID}" --gid "${ANDREA_GID}" --shell /bin/bash "${ANDREA_USER}"; fi \
    && mkdir -p /app /opt/genespider2/compiled /tmp/mcr_cache \
    && chmod 1777 /tmp/mcr_cache

WORKDIR /app

COPY wrappers/simulation_data_tools/simulators/genespider2/run_simulator.py /app/run_simulator.py
COPY wrappers/simulation_data_tools/simulators/genespider2/compiled/run_genespider2 /opt/genespider2/compiled/run_genespider2
COPY wrappers/simulation_data_tools/simulators/genespider2/compiled/run_run_genespider2.sh /opt/genespider2/compiled/run_run_genespider2.sh

RUN chmod +x /app/run_simulator.py /opt/genespider2/compiled/run_genespider2 /opt/genespider2/compiled/run_run_genespider2.sh \
    && python3 -m py_compile /app/run_simulator.py

ENTRYPOINT ["python3", "/app/run_simulator.py", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
