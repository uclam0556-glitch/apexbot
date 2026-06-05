"""
APEX v11.0 — services/engine/data_health.py
=============================================
DEPRECATED SHIM: This module is replaced by services/data/validator.py

The old compute_data_health() function is now a thin wrapper around
the institutional DataValidator class. All behavior is preserved
via the backward-compatibility shim in validator.py.

DO NOT add new logic here.
DO NOT use this module in new code.
REMOVE this file in v11.1 after all call-sites are migrated to DataValidator.validate()
"""
import logging

logger = logging.getLogger(__name__)

# Re-export the legacy-compatible wrapper from the new institutional module
from services.data.validator import compute_data_health  # noqa: F401, E402

logger.debug(
    "[DEPRECATED] services/engine/data_health.py is a shim. "
    "Migrate to services/data/validator.DataValidator"
)
