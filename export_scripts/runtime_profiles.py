"""Optional downstream runtime customization for neutral extraction records.

The extractor deliberately emits project-neutral data by default.  A consumer
that needs application-specific names or compatibility behavior can pass a
profile object to :func:`apply_candidate_profile` and
:func:`apply_diagnostic_profile`, or to ``export_script_candidates.main``.

Profiles are intentionally duck-typed so downstream projects do not need to
depend on an extractor-specific base class.  A profile may implement either or
both of these methods::

    customize_candidates(candidates) -> candidates
    customize_diagnostics(diagnostics) -> diagnostics

Both helpers copy their input before invoking the profile, preventing a profile
from mutating the neutral records retained by its caller.
"""

from copy import deepcopy


def apply_candidate_profile(candidates, profile=None):
    """Return candidates customized by an explicitly supplied profile."""
    records = deepcopy(candidates)
    if profile is None:
        return records
    customize = getattr(profile, "customize_candidates", None)
    if customize is None:
        return records
    customized = customize(records)
    return records if customized is None else customized


def apply_diagnostic_profile(diagnostics, profile=None):
    """Return diagnostics customized by an explicitly supplied profile."""
    records = deepcopy(diagnostics)
    if profile is None:
        return records
    customize = getattr(profile, "customize_diagnostics", None)
    if customize is None:
        return records
    customized = customize(records)
    return records if customized is None else customized
