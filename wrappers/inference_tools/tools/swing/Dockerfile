FROM --platform=linux/amd64 python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e

LABEL org.opencontainers.image.source="https://github.com/bagherilab/SWING" \
      org.opencontainers.image.revision="591efc7c7c8e6bae2a610a833ec727047f7f1673" \
      org.opencontainers.image.description="ANDREA SWING Python integration; reviewed compatibility and deterministic resource limits"

WORKDIR /app
RUN printf '%s\n' \
      'numpy==1.26.4 --hash=sha256:666dbfb6ec68962c033a450943ded891bed2d54e6755e35e5835d63f4f6931d5' \
      'scipy==1.12.0 --hash=sha256:8b8066bce124ee5531d12a74b617d9ac0ea59245246410e19bca549656d9a40a' \
      'scikit-learn==1.4.2 --hash=sha256:44c62f2b124848a28fd695db5bc4da019287abf390bfce602ddc8aa1ec186aae' \
      'pandas==2.2.2 --hash=sha256:6d2123dc9ad6a814bcdea0f099885276b31b24f7edf40f6cdbc0912672e22eee' \
      'joblib==1.4.2 --hash=sha256:06d478d5674cbc267e7496a410ee875abd68e4340feff4490bcb7afb88060ae6' \
      'threadpoolctl==3.5.0 --hash=sha256:56c1e26c150397e58c4926da8eeee87533b1e32bef131bd4bf6a2f45f3185467' \
      'python-dateutil==2.9.0.post0 --hash=sha256:a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427' \
      'pytz==2024.1 --hash=sha256:328171f4e3623139da4983451950b28e95ac706e13f3f2630a879749e7a8b319' \
      'tzdata==2024.1 --hash=sha256:9068bc196136463f5245e51efda838afa15aaeca9903f49050dfa2679db4d252' \
      'six==1.16.0 --hash=sha256:8abb2f1d86890a2dfb989f9a77cfcfd3e47c2a354b01111771326f8aa26e0254' \
    | python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /dev/stdin
COPY run_tool.py /app/run_tool.py
RUN python /app/run_tool.py --prepare-upstream --output-dir /app
COPY run_tool.sh /app/run_tool.sh
COPY _run_tool_common.py /app/_run_tool_common.py
COPY _column_metadata.py /app/_column_metadata.py
RUN chmod +x /app/run_tool.sh
ENTRYPOINT ["/app/run_tool.sh"]
