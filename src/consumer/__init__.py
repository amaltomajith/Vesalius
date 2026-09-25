"""CV consumer: a small, deliberately independent client of the published `volume_contract`.

It depends on numpy and the standard library only -- no `pipeline`, no `api`, no database or
object-store client -- so it can genuinely refuse the pipeline's output rather than share its
assumptions. Public surface: `load`, `run`, `compute_features`, `ContractViolation`.
"""

from consumer.contract import ContractViolation, IntegrityError

__all__ = ["ContractViolation", "IntegrityError"]
