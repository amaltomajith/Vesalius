"""S11 -- Export: RAS-to-glTF axis conversion + centring, GLB/volume/thumbnail/manifest into
gold.

Public surface: `service.export_series` / `export_batch`. S10's optimised mesh is read only via
its `Artifact` row + `s10_mesh_postprocess.mesh_io` -- never `service` or `mesh_postprocess`.
The intensity volume comes only from `s7_deface.gate.open_defacing_gate`.
"""
