"""
APEX Trading System v10.5
core/logic_versioner.py
"""

from typing import Dict, Any

class LogicVersioner:
    """
    Обеспечивает строгое версионирование логики для A/B тестов.
    """
    
    CURRENT_VERSION = "10.5.0"
    
    CHANGELOG = {
        "10.5.0": [
            "TimescaleDB migration",
            "Look-ahead bias strict prevention in SMC",
            "Transaction costs realistic model",
            "Kelly criterion position sizing",
            "Correlation & Circuit breakers added"
        ],
        "10.4.0": [
            "Shadow monitor fixes",
            "MTF alignment improvements"
        ]
    }
    
    @classmethod
    def get_version(cls) -> str:
        return cls.CURRENT_VERSION
        
    @classmethod
    def get_info(cls) -> Dict[str, Any]:
        return {
            "version": cls.CURRENT_VERSION,
            "changelog": cls.CHANGELOG.get(cls.CURRENT_VERSION, [])
        }
