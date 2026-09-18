"""Financial Engineering & Basel II/III Risk Modeling for CreditFlow.

Implements core banking and regulatory credit risk models:
1. Basel II/III Expected Loss Framework:
   EL = PD * LGD * EAD
   - PD: Probability of Default (from calibrated ML model)
   - LGD: Loss Given Default (derived from collateral type and LTV)
   - EAD: Exposure at Default (drawn balance + undrawn commitment * CCF)
   - Capital Requirement (K) & RWA under Basel II Standardized Approach.
2. Risk-Based Pricing Engine (Cost of Funds + Opex + Credit Cost + Margin).
3. Maximum Safe Credit Line calculation based on Debt Service Ratio (DSR <= 45%).
4. Detailed Loan Amortization Schedule (Equal Monthly Installment & Reducing Balance).
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Tuple


class CollateralType(str, Enum):
    """Supported collateral types for credit underwriting."""
    NONE = "NONE"                     # Tín chấp không TSBĐ
    REAL_ESTATE = "REAL_ESTATE"       # Bất động sản (sổ đỏ / sổ hồng)
    VEHICLE = "VEHICLE"               # Phương tiện giao thông (ô tô, xe tải)
    SAVINGS = "SAVINGS"               # Sổ tiết kiệm / Tiền gửi có kỳ hạn


# Base Loss Given Default (LGD) reference values under Basel guidelines
BASE_LGD_TABLE = {
    CollateralType.NONE.value: 0.65,          # 65% for unsecured retail
    CollateralType.REAL_ESTATE.value: 0.20,   # 20% for residential property
    CollateralType.VEHICLE.value: 0.40,       # 40% for movable vehicle asset
    CollateralType.SAVINGS.value: 0.05,       # 5% for cash / deposit pledge
}

# Master Rating Scale mapping PD to Credit Grades
MASTER_SCALE_BUCKETS = [
    (0.010, "AAA", "Rủi ro cực thấp — Tiêu chuẩn vàng"),
    (0.025, "AA",  "Rủi ro rất thấp — Ổn định cao"),
    (0.050, "A",   "Rủi ro thấp — Tín nhiệm tốt"),
    (0.100, "BBB", "Rủi ro trung bình thấp — Đủ điều kiện"),
    (0.180, "BB",  "Rủi ro trung bình — Cần giám sát"),
    (0.300, "B",   "Rủi ro cao — Dễ tổn thương"),
    (0.500, "CCC", "Rủi ro rất cao — Nguy cơ vỡ nợ rõ rệt"),
    (1.000, "HR",  "High Risk — Khuyến nghị từ chối"),
]


def map_master_scale_rating(pd: float) -> Tuple[str, str]:
    """Map probability of default (PD) to Master Rating Scale grade and description."""
    clamped_pd = max(0.0, min(1.0, float(pd)))
    for threshold, grade, desc in MASTER_SCALE_BUCKETS:
        if clamped_pd <= threshold:
            return grade, desc
    return "HR", "High Risk — Khuyến nghị từ chối"


def calculate_lgd(
    collateral_type: str | CollateralType = CollateralType.NONE,
    collateral_value: float = 0.0,
    loan_amount: float = 0.0,
) -> Tuple[float, float]:
    """Calculate Loss Given Default (LGD) and Loan-to-Value (LTV) ratio.

    Returns:
        (lgd, ltv): lgd between [0.05, 0.90], ltv >= 0.0
    """
    col_str = str(getattr(collateral_type, "value", collateral_type)).upper().strip()
    base_lgd = BASE_LGD_TABLE.get(col_str, 0.65)

    if collateral_value <= 0.0 or loan_amount <= 0.0 or col_str == CollateralType.NONE.value:
        return round(base_lgd, 4), 0.0

    ltv = loan_amount / collateral_value

    # Over-collateralized (LTV < 50%): lower recovery risk
    if ltv <= 0.50:
        adjusted_lgd = base_lgd * 0.70
    elif ltv <= 0.80:
        adjusted_lgd = base_lgd
    elif ltv <= 1.00:
        adjusted_lgd = base_lgd * 1.25
    else:
        # Under-collateralized: blend with unsecured rate
        excess_ratio = min(1.0, (ltv - 1.0))
        adjusted_lgd = base_lgd + excess_ratio * (0.65 - base_lgd)

    clamped_lgd = max(0.05, min(0.90, adjusted_lgd))
    return round(clamped_lgd, 4), round(ltv, 4)


def calculate_ead(
    loan_amount: float,
    committed_undrawn: float = 0.0,
    ccf: float = 0.75,
) -> float:
    """Calculate Exposure at Default (EAD) with Credit Conversion Factor (CCF)."""
    principal = max(0.0, float(loan_amount))
    undrawn = max(0.0, float(committed_undrawn))
    ead = principal + undrawn * max(0.0, min(1.0, float(ccf)))
    return round(ead, 2)


def calculate_basel_metrics(
    pd: float,
    loan_amount: float,
    collateral_type: str | CollateralType = CollateralType.NONE,
    collateral_value: float = 0.0,
    committed_undrawn: float = 0.0,
) -> Dict[str, Any]:
    """Compute Basel II/III Credit Risk Metrics (PD, LGD, EAD, EL, RWA, Capital).

    Formulae:
        EL = PD * LGD * EAD
        RWA = EAD * RiskWeight (Standardized Basel Approach: 75% for retail, 35% residential)
        Capital Requirement (K) = 8% * RWA
    """
    safe_pd = max(0.0, min(1.0, float(pd)))
    safe_loan = max(0.0, float(loan_amount))
    lgd, ltv = calculate_lgd(collateral_type, collateral_value, safe_loan)
    ead = calculate_ead(safe_loan, committed_undrawn)

    expected_loss = safe_pd * lgd * ead

    # Basel Standardized Risk Weight determination
    col_str = str(getattr(collateral_type, "value", collateral_type)).upper().strip()
    if col_str == CollateralType.REAL_ESTATE.value and ltv <= 0.80 and ltv > 0:
        risk_weight = 0.35   # 35% for qualifying residential mortgages
    elif col_str == CollateralType.SAVINGS.value:
        risk_weight = 0.00   # 0% for cash / deposit pledged loans
    elif col_str == CollateralType.VEHICLE.value and ltv <= 0.70 and ltv > 0:
        risk_weight = 0.50   # 50% for secured vehicle financing
    else:
        risk_weight = 0.75   # 75% for general retail unsecured lending

    rwa = ead * risk_weight
    regulatory_capital = rwa * 0.08  # 8% minimum capital adequacy ratio (CAR)

    rating_grade, rating_desc = map_master_scale_rating(safe_pd)

    return {
        "pd": round(safe_pd, 4),
        "lgd": round(lgd, 4),
        "ead": round(ead, 2),
        "expected_loss": round(expected_loss, 2),
        "expected_loss_ratio": round(expected_loss / safe_loan, 4) if safe_loan > 0 else 0.0,
        "collateral_type": col_str,
        "collateral_value": round(collateral_value, 2),
        "ltv_ratio": round(ltv, 4),
        "risk_weight": round(risk_weight, 2),
        "rwa": round(rwa, 2),
        "regulatory_capital": round(regulatory_capital, 2),
        "rating_grade": rating_grade,
        "rating_desc": rating_desc,
    }


def calculate_risk_based_pricing(
    pd: float,
    expected_loss: float,
    loan_amount: float,
    base_cost_of_funds: float = 0.065,    # 6.5%/năm lãi suất nguồn vốn
    operating_cost_rate: float = 0.025,   # 2.5%/năm chi phí vận hành
    target_profit_margin: float = 0.030,  # 3.0%/năm biên lợi nhuận mục tiêu
    max_legal_annual_rate: float = 0.20,  # 20.0%/năm trần lãi suất theo Bộ luật Dân sự
) -> Dict[str, Any]:
    """Calculate commercial risk-based interest rate for loan underwriting.

    Lãi suất = Chi phí vốn + Vận hành + Bù rủi ro (EL / Loan) + Biên lợi nhuận
    """
    safe_loan = max(1.0, float(loan_amount))
    credit_risk_cost = expected_loss / safe_loan

    uncapped_annual_rate = (
        base_cost_of_funds + operating_cost_rate + credit_risk_cost + target_profit_margin
    )
    recommended_annual_rate = min(uncapped_annual_rate, max_legal_annual_rate)

    return {
        "base_cost_of_funds": round(base_cost_of_funds, 4),
        "operating_cost_rate": round(operating_cost_rate, 4),
        "credit_risk_cost": round(credit_risk_cost, 4),
        "target_profit_margin": round(target_profit_margin, 4),
        "uncapped_annual_rate": round(uncapped_annual_rate, 4),
        "recommended_annual_rate": round(recommended_annual_rate, 4),
        "monthly_interest_rate": round(recommended_annual_rate / 12.0, 6),
        "is_rate_capped": uncapped_annual_rate > max_legal_annual_rate,
        "max_legal_annual_rate": round(max_legal_annual_rate, 4),
    }


def calculate_max_safe_credit_limit(
    monthly_income: float,
    existing_debt: float = 0.0,
    loan_term_months: int = 36,
    annual_interest_rate: float = 0.12,
    max_dsr: float = 0.45,  # Debt Service Ratio: tối đa 45% thu nhập trả nợ
) -> Dict[str, Any]:
    """Calculate maximum safe credit line based on Debt Service Ratio (DSR).

    Ensures applicant's total monthly debt payments never exceed max_dsr * income.
    """
    income = max(0.0, float(monthly_income))
    debt = max(0.0, float(existing_debt))
    term = max(1, int(loan_term_months))
    r = max(0.0001, float(annual_interest_rate) / 12.0)

    # Estimate monthly obligation of existing debt (assuming amortized over 24 months at 12%)
    existing_monthly_payment = (debt * 0.05) if debt > 0 else 0.0

    max_monthly_capacity = income * max_dsr
    available_monthly_installment = max(0.0, max_monthly_capacity - existing_monthly_payment)

    # Present value of annuity formula: PV = PMT * (1 - (1+r)^-N) / r
    discount_factor = (1.0 - math.pow(1.0 + r, -term)) / r
    max_safe_limit = available_monthly_installment * discount_factor

    # Round to nearest 500,000 VND
    rounded_limit = math.floor(max_safe_limit / 500_000.0) * 500_000.0

    return {
        "monthly_income": round(income, 2),
        "max_dsr": round(max_dsr, 2),
        "max_monthly_capacity": round(max_monthly_capacity, 2),
        "existing_monthly_payment": round(existing_monthly_payment, 2),
        "available_monthly_installment": round(available_monthly_installment, 2),
        "loan_term_months": term,
        "annual_interest_rate": round(annual_interest_rate, 4),
        "max_safe_limit": max(0.0, rounded_limit),
    }


def generate_amortization_schedule(
    loan_amount: float,
    annual_rate: float,
    term_months: int,
    method: str = "EMI",
) -> List[Dict[str, Any]]:
    """Generate detailed month-by-month repayment schedule.

    Methods:
        - "EMI": Equal Monthly Installment (Niên kim cố định).
        - "REDUCING": Reducing balance (Gốc chia đều, lãi giảm dần).
    """
    principal = max(0.0, float(loan_amount))
    months = max(1, int(term_months))
    monthly_rate = max(0.0, float(annual_rate) / 12.0)

    schedule: List[Dict[str, Any]] = []
    balance = principal

    if method.upper() == "EMI" and monthly_rate > 0:
        # EMI = P * r * (1+r)^n / ((1+r)^n - 1)
        factor = math.pow(1.0 + monthly_rate, months)
        monthly_payment = principal * (monthly_rate * factor) / (factor - 1.0)

        for m in range(1, months + 1):
            interest = balance * monthly_rate
            principal_part = monthly_payment - interest
            if m == months:
                principal_part = balance
                monthly_payment = principal_part + interest
            end_balance = max(0.0, balance - principal_part)

            schedule.append({
                "period": m,
                "starting_balance": round(balance, 2),
                "principal_payment": round(principal_part, 2),
                "interest_payment": round(interest, 2),
                "total_installment": round(monthly_payment, 2),
                "ending_balance": round(end_balance, 2),
            })
            balance = end_balance
    else:
        # Straight-line principal method
        fixed_principal = principal / months
        for m in range(1, months + 1):
            interest = balance * monthly_rate
            installment = fixed_principal + interest
            end_balance = max(0.0, balance - fixed_principal)
            schedule.append({
                "period": m,
                "starting_balance": round(balance, 2),
                "principal_payment": round(fixed_principal, 2),
                "interest_payment": round(interest, 2),
                "total_installment": round(installment, 2),
                "ending_balance": round(end_balance, 2),
            })
            balance = end_balance

    return schedule
