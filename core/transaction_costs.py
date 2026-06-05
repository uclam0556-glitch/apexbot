"""
APEX v11.0 — core/transaction_costs.py
=======================================
DEPRECATED SHIM: This module is replaced by services/execution/transaction_cost_model.py

This file exists only to prevent ImportError during the v10.5 → v11.0 migration.
The class `TransactionCostModel` here is a thin facade that delegates to the
new institutional implementation.

DO NOT add new logic here.
DO NOT use this module in new code.
REMOVE this file in v11.1 after all call-sites are migrated.
"""
import logging

logger = logging.getLogger(__name__)

# Re-export new implementation under the old name for backward compatibility
from services.execution.transaction_cost_model import (  # noqa: F401, E402
    TransactionCostModel,
)

logger.info(
    "[DEPRECATED] core/transaction_costs.py is a shim. "
    "Migrate to services/execution/transaction_cost_model.py"
)

# Legacy constant preserved for call sites that import it directly
MINIMUM_VOLUME_24H_USD = 500_000
