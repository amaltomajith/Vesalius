"""S10 -- Mesh Post-processing: largest component, degenerate cleanup, Taubin smoothing,
quadric decimation, normals last.

Public surface: `service.postprocess_mesh_series` / `postprocess_mesh_batch`. S9's raw mesh is
read only via its `Artifact` row + `s9_surface_extraction.mesh_io` -- never `service` or
`mesh_extract`.
"""
