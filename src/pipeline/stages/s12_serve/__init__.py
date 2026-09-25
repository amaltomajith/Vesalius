"""S12 -- Serve: catalog search, gold-only artifact retrieval by presigned URL, lineage graph,
quality report, published contracts.

Public surface: `queries` (read-only, serving-role session), `presign.presign_gold_url`, `models`.
The HTTP layer is `api/routers/catalog.py`. S12 reads the catalog and gold only and records no
transform_run (it produces no artifact; per-request runs would also need a write grant the
serving role deliberately lacks).
"""
