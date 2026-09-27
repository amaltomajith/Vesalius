# ---- the Phantom app (viewer/, React/Vite): built here, served by FastAPI at /app/ ----------------
# The prebuild step vendors MediaPipe (WASM from node_modules, hand model hash-pinned) so the served
# viewer makes no CDN request at runtime. The demo study (viewer/public/demo) is included when it
# was bundled locally (scripts/bundle_viewer_demo.py); it is never in git.
FROM node:22-slim AS viewer
WORKDIR /viewer
COPY viewer/package.json viewer/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY viewer ./
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

# tesseract-ocr: a real production dependency, not a test convenience -- S4 (burned-in
# pixel redaction) and S5 (independent OCR verification) need the actual binary wherever
# they run. pytesseract only wraps it; it does nothing on its own.
# fonts-dejavu-core: python:3.11-slim ships no TrueType fonts at all, so PIL falls back to
# a tiny built-in bitmap font -- fine for humans, but its OCR confidence/accuracy is poor
# even after upscaling (blocky edges). A real scalable font is needed for burned-in-text
# test fixtures (tests/fixtures/dicom_factory.py) and OCR-dependent tests to be realistic.
# No GL libraries: they were only for pymeshlab, which S10 no longer uses (fast-simplification
# is plain C++ with no GL linkage and ships aarch64 wheels; pymeshlab ships none).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# S8 structure mode: TotalSegmentator (Apache-2.0; only the Apache-2.0 `total` task weights are
# used, see LICENSES.md). CPU-only PyTorch first, from PyTorch's CPU index, so the TotalSegmentator
# install below finds torch already satisfied and never pulls the multi-GB CUDA build. torchvision
# must come from the same index: the PyPI build does not match a +cpu torch ("operator
# torchvision::nms does not exist"). Weights live in the totalseg_weights volume (TOTALSEG_HOME_DIR);
# usage statistics are switched off by the S8 code before every run, not here.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu         torch==2.14.0+cpu torchvision==0.29.0+cpu     && pip install --no-cache-dir TotalSegmentator==2.18.0
ENV TOTALSEG_HOME_DIR=/weights

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests ./tests
# the viewer's source, for the guard tests that scan it (tests/unit/test_viewer_serving.py)
COPY viewer/src ./viewer/src
# demo data loaders ("Load 3D Studies.bat" runs scripts/load_demo_studies.py in this container)
COPY scripts ./scripts

RUN pip install --no-cache-dir -e .

# pytest: the demo page's "Prove it" buttons run the proof tests (tests/proofs/) inside this container.
RUN pip install --no-cache-dir pytest

COPY --from=viewer /viewer/dist /app/app_dist
# the full-body atlas (assets/atlas, CC-BY-SA, built by scripts/build_atlas.py), served at /app/atlas/
COPY assets/atlas /app/assets/atlas

COPY docker/api/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
