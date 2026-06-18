FROM python:3.10-slim-bookworm

ARG DIGNET_REF=5109401ac242d2b671156eab4b4a5fabd808b612

ENV PYTHONUNBUFFERED=1 \
    DIGNET_HOME=/opt/DigNet \
    DIGNET_REF=${DIGNET_REF} \
    CUDA_VISIBLE_DEVICES="" \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    MPLCONFIGDIR=/tmp/matplotlib

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip wheel \
  && python -m pip install --no-cache-dir setuptools==67.6.0 \
  && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.0.0+cpu \
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
    powerlaw==1.5

RUN git clone https://github.com/zpliulab/DigNet.git /opt/DigNet \
  && git -C /opt/DigNet checkout "${DIGNET_REF}" \
  && test "$(git -C /opt/DigNet rev-parse HEAD)" = "${DIGNET_REF}" \
  && python -m zipfile -e /opt/DigNet/pre_train/pre_train.zip /opt/DigNet/pre_train \
  && python -m zipfile -e /opt/DigNet/pre_train/pre_train_2.zip /opt/DigNet/pre_train \
  && test -f /opt/DigNet/pre_train/S33_Cancer_cell_checkpoint_pre_train_20240326.pth \
  && test -f /opt/DigNet/result/S33_Cancer_cell_pca_model.pkl \
  && test -f /opt/DigNet/pathway/kegg/KEGG_all_pathway.pkl \
  && test -f /opt/DigNet/pathway/Regnetwork/2022.human.source \
  && test -f /opt/DigNet/GRN/TF.txt \
  && rm -rf /opt/DigNet/.git

ENV PYTHONPATH="/app:/opt/DigNet:${PYTHONPATH}"

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.py /app/run_tool.py
COPY _run_tool_common.py /app/_run_tool_common.py

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
