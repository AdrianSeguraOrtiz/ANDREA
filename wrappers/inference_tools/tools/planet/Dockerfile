FROM python:3.10-slim-bookworm

ARG PLANET_REF=3d8be3ca788dc436e8d2c888facdeffe49553c61

ENV PYTHONUNBUFFERED=1 \
    PLANET_HOME=/opt/project-Planet \
    PLANET_REF=${PLANET_REF} \
    CUDA_VISIBLE_DEVICES="" \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    MPLCONFIGDIR=/tmp/matplotlib

RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/g' /etc/apt/sources.list.d/debian.sources \
  && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    unrar \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip wheel \
  && python -m pip install --no-cache-dir setuptools==67.6.0 \
  && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.0.1+cpu \
  && python -m pip install --no-cache-dir \
    numpy==1.24.4 \
    pandas==1.5.3 \
    scipy==1.10.1 \
    scikit-learn==1.2.2 \
    networkx==3.1 \
    joblib==1.2.0 \
    tqdm==4.65.0 \
    torch-geometric==2.3.1 \
    torchmetrics==0.11.4 \
    matplotlib==3.6.3 \
    seaborn==0.12.2 \
    powerlaw==1.5 \
    pingouin==0.5.3

RUN git clone https://github.com/wangchuanyuan1/project-Planet.git /opt/project-Planet \
  && git -C /opt/project-Planet checkout "${PLANET_REF}" \
  && test "$(git -C /opt/project-Planet rev-parse HEAD)" = "${PLANET_REF}" \
  && unrar x -idq -o+ /opt/project-Planet/pathway/kegg.rar /opt/project-Planet/pathway/ \
  && unrar x -idq -o+ /opt/project-Planet/pathway/Regnetwork.rar /opt/project-Planet/pathway/ \
  && test -f /opt/project-Planet/pre-train/Pre-training_weights_on_simulated_datasets.pth \
  && test -f /opt/project-Planet/pathway/kegg/KEGG_all_pathway.pkl \
  && test -f /opt/project-Planet/pathway/Regnetwork/2022.human.source \
  && test -s /opt/project-Planet/GRN/TF.txt \
  && ! grep -qi "<!doctype" /opt/project-Planet/GRN/TF.txt \
  && rm -rf /opt/project-Planet/.git

ENV PYTHONPATH="/app:/opt/project-Planet:${PYTHONPATH}"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
