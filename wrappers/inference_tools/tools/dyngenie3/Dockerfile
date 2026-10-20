FROM --platform=linux/amd64 python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e

LABEL org.opencontainers.image.source="https://github.com/vahuynh/dynGENIE3" \
      org.opencontainers.image.revision="27eddd960d50e82791a8e0e5f8fcf6d679fe3eac" \
      org.opencontainers.image.description="ANDREA dynGENIE3 Python integration; reviewed compatibility and deterministic per-target seed patches"

WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /app/requirements.lock
COPY patch_upstream.py /app/patch_upstream.py
RUN python -c "import urllib.request; from patch_upstream import UPSTREAM_URL, patched_source; raw=urllib.request.urlopen(UPSTREAM_URL, timeout=60).read(); open('/app/dynGENIE3.original.py','wb').write(raw); open('/app/dynGENIE3.py','w').write(patched_source(raw))"
COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py
RUN chmod +x /app/run_tool.sh
ENTRYPOINT ["/app/run_tool.sh"]
