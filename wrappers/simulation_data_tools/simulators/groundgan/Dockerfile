FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    GROUNDGAN_SRC=/opt/groundgan/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.2.2 \
    && python -m pip install \
        anndata==0.10.8 \
        h5py==3.11.0 \
        matplotlib==3.8.4 \
        numpy==1.26.4 \
        pandas==2.2.2 \
        scikit-learn==1.5.0 \
        scipy==1.13.1 \
        tensorboard==2.16.2

RUN git clone https://github.com/Emad-COMBINE-lab/GRouNdGAN.git /opt/groundgan \
    && cd /opt/groundgan \
    && git checkout 2df087f9144081c46eb6ce0a1daadd273adcc50a

COPY wrappers/simulation_data_tools/simulators/groundgan/run_simulator.py /opt/andrea/run_simulator.py
COPY wrappers/simulation_data_tools/simulators/groundgan/build_toy_bundles.py /opt/andrea/build_toy_bundles.py
RUN python /opt/andrea/build_toy_bundles.py \
    && rm /opt/andrea/build_toy_bundles.py
RUN chmod +x /opt/andrea/run_simulator.py

ENTRYPOINT ["python", "/opt/andrea/run_simulator.py"]
