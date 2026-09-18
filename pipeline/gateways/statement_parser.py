"""Bank Statement & Cashflow Volatility Parser Simulator.

Analyzes borrower transaction statements (via Open Banking / VietQR APIs):
1. Inflow verification (actual salary deposit vs self-declared income).
2. Cashflow stability and volatility analysis.
3. Transaction pattern scanning for high-risk flags (predatory loan apps, gambling).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class StatementAnalysisResult(BaseModel):
    """Normalized bank statement analysis summary."""
    verified_average_salary: float = Field(..., ge=0.0, description="Lương thực nhận trung bình qua sao kê (VND)")
    salary_confidence: str = "HIGH"  # HIGH, MEDIUM, LOW
    salary_gap_ratio: float = Field(..., description="Tỷ lệ chênh lệch giữa lương khai báo và sao kê thực tế")
    cashflow_volatility: float = Field(..., ge=0.0, description="Hệ số biến động dòng tiền (0.0: rất ổn định -> 1.0: bấp bênh)")
    high_risk_transaction_count: int = Field(default=0, ge=0)
    risk_keywords_detected: List[str] = Field(default_factory=list)
    statement_health_score: float = Field(..., ge=0.0, le=1.0, description="Điểm sức khỏe dòng tiền (0.0 - 1.0)")
    analyst_summary: str


RISK_KEYWORDS = [
    # Cờ bạc online
    "kubet", "thabet", "fun88", "w88", "188bet", "rikvip", "b52", "sunwin", "go88",
    # App vay nóng / tín dụng đen
    "dongplus", "senmo", "robocash", "atmonline", "vayvnd", "vaycaptoc", "doctordong", "oncredit"
]


def analyze_bank_statement(
    declared_income: float,
    transactions: Optional[List[Dict[str, Any]]] = None,
) -> StatementAnalysisResult:
    """Analyze transaction inflows and detect cashflow risks.

    If raw transactions are provided, analyzes them directly.
    Otherwise, uses realistic financial heuristic simulation based on declared income.
    """
    safe_declared = max(1.0, float(declared_income))

    if transactions and len(transactions) > 0:
        # Real transactions analysis
        salary_inflows = [
            float(t.get("amount", 0.0))
            for t in transactions
            if t.get("amount", 0.0) > 0 and any(k in str(t.get("description", "")).lower() for k in ["luong", "salary", "thulao", "cong ty", "ck"])
        ]
        if salary_inflows:
            avg_salary = sum(salary_inflows) / len(salary_inflows)
            std_dev = (sum((x - avg_salary) ** 2 for x in salary_inflows) / len(salary_inflows)) ** 0.5
            volatility = min(1.0, std_dev / avg_salary) if avg_salary > 0 else 0.5
        else:
            avg_salary = safe_declared * 0.90
            volatility = 0.25

        # Scan descriptions for high risk keywords
        detected_keywords = []
        for t in transactions:
            desc = str(t.get("description", "")).lower()
            for kw in RISK_KEYWORDS:
                if kw in desc and kw not in detected_keywords:
                    detected_keywords.append(kw)

        high_risk_count = len(detected_keywords)
    else:
        # Heuristic simulation: most formal sector workers have small variance
        avg_salary = safe_declared * 0.96
        volatility = 0.12
        detected_keywords = []
        high_risk_count = 0

    gap_ratio = abs(safe_declared - avg_salary) / safe_declared

    # Health score calculation
    health = 1.0 - (volatility * 0.4) - (min(1.0, gap_ratio) * 0.3) - (min(1.0, high_risk_count * 0.2))
    health = max(0.0, min(1.0, health))

    if gap_ratio > 0.4:
        confidence = "LOW"
        summary = f"Cảnh báo: Thu nhập thực tế qua sao kê thấp hơn đáng kể so với khai báo ({gap_ratio*100:.1f}% lệch)."
    elif high_risk_count > 0:
        confidence = "MEDIUM"
        summary = f"Phát hiện {high_risk_count} giao dịch có dấu hiệu vay app ngoài hoặc cờ bạc trực tuyến."
    else:
        confidence = "HIGH"
        summary = "Dòng tiền lương qua tài khoản ổn định, đúng định kỳ hàng tháng."

    return StatementAnalysisResult(
        verified_average_salary=round(avg_salary, 2),
        salary_confidence=confidence,
        salary_gap_ratio=round(gap_ratio, 4),
        cashflow_volatility=round(volatility, 4),
        high_risk_transaction_count=high_risk_count,
        risk_keywords_detected=detected_keywords,
        statement_health_score=round(health, 2),
        analyst_summary=summary,
    )
