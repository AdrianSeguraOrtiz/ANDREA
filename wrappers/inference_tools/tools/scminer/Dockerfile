FROM rocker/r2u:24.04

ARG SCMINER_REF=48d4bc4e7fe7ccc880e5816857f264eb7c2161e9
ARG SJARACNE_REF=0.2.1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    git \
    make \
    nodejs \
    python3 \
    python3-dev \
    python3-pip \
    r-bioc-biobase \
    r-bioc-limma \
    r-cran-anndata \
    r-cran-dplyr \
    r-cran-ggplot2 \
    r-cran-gridextra \
    r-cran-igraph \
    r-cran-jsonlite \
    r-cran-matrix \
    r-cran-pheatmap \
    r-cran-rcolorbrewer \
    r-cran-remotes \
    r-cran-reshape2 \
    r-cran-rmarkdown \
    r-cran-sessioninfo \
  && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    cwltool \
    numpy \
    pandas \
    scipy \
  && git clone --depth 1 --branch "${SJARACNE_REF}" https://github.com/jyyulab/SJARACNe.git /tmp/SJARACNe \
  && python3 -m pip install --break-system-packages --no-cache-dir --no-deps --ignore-requires-python /tmp/SJARACNe \
  && rm -rf /tmp/SJARACNe

RUN python3 - <<'PY'
import pathlib
import stat

import SJARACNe

root = pathlib.Path(SJARACNe.__file__).resolve().parent
for script in (root / "bin").glob("*.py"):
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

for cwl in (root / "cwl").glob("*.cwl"):
    text = cwl.read_text()
    if "type: float" in text:
        cwl.write_text(text.replace("type: float", "type: double"))

sjaracne_cwl = root / "cwl" / "sjaracne.cwl"
text = sjaracne_cwl.read_text()
if "InlineJavascriptRequirement" not in text:
    text = text.replace(
        "baseCommand: sjaracne.exe\n",
        "requirements:\n  InlineJavascriptRequirement: {}\n\nbaseCommand: sjaracne.exe\n",
    )
text = text.replace(
    "  p_value:\n    type: double\n    inputBinding:\n      position: 4\n      prefix: -p\n",
    "  p_value:\n    type: double\n    inputBinding:\n      position: 4\n      prefix: -p\n      valueFrom: $(inputs.p_value.toPrecision(12))\n",
)
sjaracne_cwl.write_text(text)
PY

WORKDIR /app

RUN R -q -e "remotes::install_github('jyyulab/scMINER', ref='${SCMINER_REF}', dependencies=FALSE, upgrade='never', build_vignettes=FALSE)" \
  && R -q -e "stopifnot(as.character(utils::packageVersion('scMINER')) == '1.1.0')"

RUN sed -i '/options(bspm.version.check=FALSE)/a options(bspm.sudo = TRUE)' /usr/lib/R/etc/Rprofile.site

COPY run_tool.sh /app/run_tool.sh
COPY run_tool.R /app/run_tool.R

RUN chmod +x /app/run_tool.sh

ENTRYPOINT ["/app/run_tool.sh"]
