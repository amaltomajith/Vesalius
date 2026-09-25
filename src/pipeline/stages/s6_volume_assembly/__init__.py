"""S6 -- Volume Assembly. See refs/STAGES.md S6, refs/DICOM.md Part 2.

The stage explicitly called out (DICOM.md/STAGES.md) as most likely to be silently wrong:
output that runs without error and is geometrically incorrect. Every geometry value this
package relies on is either recomputed fresh from the actual silver bytes being assembled
or cross-checked against S3's cache rather than trusted blindly -- see service.py.
"""
