"""Process-wide singletons behind the demo interface.

ONE lock is shared by the scan runner and the proof runner, so at most one heavy job runs at a
time in this process (memory protection). Tests replace these objects.
"""

from __future__ import annotations

import threading
from typing import Any

from pipeline.demo.proofs import ProofRunner
from pipeline.demo.runner import PipelineRunner, real_deps

HEAVY_LOCK = threading.Lock()
RUNNER = PipelineRunner(real_deps, lock=HEAVY_LOCK)
PROOF_RUNNER = ProofRunner(lock=HEAVY_LOCK)

# latest finished job per proof key, so the report and the page can show the last result
LAST_PROOF: dict[str, dict[str, Any]] = {}
