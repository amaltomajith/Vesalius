"""Demo support for the point-and-click interface: synthetic sample scans, an in-process
pipeline runner with plain-language progress, a proof runner, and a one-page report.

Everything here is a client of the pipeline's public stage functions and of the S12 API; it adds
no pipeline behaviour. Nothing under `tests/` is imported (production code never depends on
test fixtures).
"""
