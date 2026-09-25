FROM python:3.11-slim

WORKDIR /app

# tesseract-ocr: a real production dependency, not a test convenience -- S4 (burned-in
# pixel redaction) and S5 (independent OCR verification) need the actual binary wherever
# they run. pytesseract only wraps it; it does nothing on its own.
# fonts-dejavu-core: python:3.11-slim ships no TrueType fonts at all, so PIL falls back to
# a tiny built-in bitmap font -- fine for humans, but its OCR confidence/accuracy is poor
# even after upscaling (blocky edges). A real scalable font is needed for burned-in-text
# test fixtures (tests/fixtures/dicom_factory.py) and OCR-dependent tests to be realistic.
# libgl1/libopengl0/libegl1: pymeshlab's compiled plugin .so files (S10) link against GL/EGL
# even for headless mesh decimation. Found empirically, one missing library at a time: without
# them `import pymeshlab` itself fails ("libGL.so.1: cannot open shared object file"), and even
# after libgl1 alone several of pymeshlab's plugins (including the one implementing the quadric
# decimation filter S10 calls) silently fail to load with "libOpenGL.so.0: cannot open shared
# object file", so `meshing_decimation_quadric_edge_collapse` is missing from MeshSet at runtime
# -- a plugin-loading failure, not a Python import error, so it surfaces late and confusingly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr fonts-dejavu-core libgl1 libopengl0 libegl1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests ./tests

RUN pip install --no-cache-dir -e .

COPY docker/api/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
