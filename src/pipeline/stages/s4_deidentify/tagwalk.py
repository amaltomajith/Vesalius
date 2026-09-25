"""DICOM.md's recursive sequence walker, verbatim in spirit. "PHI hides inside nested
sequences. Recurse."

s5_verify/rescan.py deliberately does NOT import this -- it writes its own independent
traversal, per the requirement that S5 be independently-written code from S4 (a
de-identifier that verifies itself proves nothing).
"""

from __future__ import annotations

from collections.abc import Callable

import pydicom
from pydicom.dataelem import DataElement


def walk(
    ds: pydicom.dataset.Dataset,
    visit: Callable[[pydicom.dataset.Dataset, DataElement], None],
) -> None:
    """Calls `visit(ds, elem)` for every element in `ds`, recursing into every item of
    every sequence (VR == "SQ") first. `list(ds)` makes a snapshot so `visit` is free to
    mutate/delete the element it's given without breaking iteration.
    """
    for elem in list(ds):
        if elem.VR == "SQ":
            for item in elem.value:
                walk(item, visit)
        visit(ds, elem)
