"""External Financial Gateways (CIC National Credit Bureau & Statement Parser)."""
from pipeline.gateways.cic_gateway import (
    query_cic_report,
    cross_validate_with_cic,
    CICReport,
)
from pipeline.gateways.statement_parser import (
    analyze_bank_statement,
    StatementAnalysisResult,
)

__all__ = [
    "query_cic_report",
    "cross_validate_with_cic",
    "CICReport",
    "analyze_bank_statement",
    "StatementAnalysisResult",
]
