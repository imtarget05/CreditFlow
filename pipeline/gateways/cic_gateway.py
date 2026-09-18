"""National Credit Information Center (CIC) B2B Gateway Simulator.

Simulates Vietnam's CIC credit rating query protocol under State Bank of Vietnam (NHNN) regulations.
Provides:
1. National ID (CCCD - 12 digits) credit registry lookup.
2. Official Debt Group history:
   - Group 1: Nợ đủ tiêu chuẩn (Dưới 10 ngày quá hạn)
   - Group 2: Nợ cần chú ý (10 - 90 ngày)
   - Group 3: Nợ dưới tiêu chuẩn (91 - 180 ngày)
   - Group 4: Nợ nghi ngờ (181 - 360 ngày)
   - Group 5: Nợ có khả năng mất vốn (Trên 360 ngày)
3. Cross-verification engine:
   - Catches dishonest applicants who declare 0 defaults while CIC records Group 3-5 bad debt.
   - Catches applicants under-reporting existing debts.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class CICReport(BaseModel):
    """Normalized CIC credit report model."""
    national_id: str = Field(..., description="Số CCCD/CMND 12 chữ số")
    query_timestamp: str
    status: str = "SUCCESS"  # SUCCESS, NOT_FOUND, ERROR
    cic_score: int = Field(..., ge=400, le=850, description="Điểm tín dụng CIC (400 - 850)")
    cic_grade: str = Field(..., description="Phân hạng tín nhiệm CIC (Hạng 1 đến 10)")
    bad_debt_group: int = Field(..., ge=1, le=5, description="Nhóm nợ cao nhất trong 36 tháng")
    bad_debt_group_name: str
    active_institutions_count: int = Field(..., ge=0, description="Số TCTD đang có dư nợ")
    total_recorded_debt: float = Field(..., ge=0.0, description="Tổng dư nợ ghi nhận tại CIC (VND)")
    overdue_days_max: int = Field(..., ge=0, description="Số ngày quá hạn lớn nhất từng ghi nhận")
    legal_warning: Optional[str] = None


# Known fixed test CCCD accounts for deterministic automated testing
KNOWN_CIC_PROFILES: Dict[str, Dict[str, Any]] = {
    # Khách hàng hạng 1 (Vàng): Không nợ xấu, điểm cực cao
    "001088012345": {
        "cic_score": 790,
        "bad_debt_group": 1,
        "active_institutions_count": 1,
        "total_recorded_debt": 15_000_000.0,
        "overdue_days_max": 0,
        "legal_warning": None,
    },
    # Khách hàng có nợ cần chú ý (Nhóm 2)
    "001092023456": {
        "cic_score": 620,
        "bad_debt_group": 2,
        "active_institutions_count": 2,
        "total_recorded_debt": 45_000_000.0,
        "overdue_days_max": 45,
        "legal_warning": "Có lịch sử chậm trả nợ trong 12 tháng qua",
    },
    # Khách hàng nợ xấu Nhóm 3 (Dưới tiêu chuẩn) - Gian lận khai 0 defaults
    "001075034567": {
        "cic_score": 480,
        "bad_debt_group": 3,
        "active_institutions_count": 3,
        "total_recorded_debt": 120_000_000.0,
        "overdue_days_max": 120,
        "legal_warning": "Khách hàng có nợ xấu nhóm 3 tại TCTD khác",
    },
    # Khách hàng nợ xấu Nhóm 5 (Mất vốn - Blacklist toàn hệ thống)
    "001065045678": {
        "cic_score": 410,
        "bad_debt_group": 5,
        "active_institutions_count": 4,
        "total_recorded_debt": 350_000_000.0,
        "overdue_days_max": 420,
        "legal_warning": "NỢ XẤU NHÓM 5: Đề nghị từ chối tuyệt đối theo Thông tư NHNN",
    },
}

GROUP_NAMES = {
    1: "Nhóm 1: Nợ đủ tiêu chuẩn",
    2: "Nhóm 2: Nợ cần chú ý",
    3: "Nhóm 3: Nợ dưới tiêu chuẩn",
    4: "Nhóm 4: Nợ nghi ngờ",
    5: "Nhóm 5: Nợ có khả năng mất vốn",
}


def _map_grade_from_score(score: int) -> str:
    if score >= 750: return "Hạng 1 (Rất tốt)"
    if score >= 700: return "Hạng 2 (Tốt)"
    if score >= 650: return "Hạng 3 (Khá)"
    if score >= 600: return "Hạng 4 (Trung bình khá)"
    if score >= 550: return "Hạng 5 (Trung bình)"
    if score >= 500: return "Hạng 6 (Dưới trung bình)"
    if score >= 450: return "Hạng 7 (Kém)"
    return "Hạng 8-10 (Rất kém / Rủi ro cao)"


def query_cic_report(national_id: str) -> CICReport:
    """Query CIC central credit bureau database by 12-digit CCCD.

    If the CCCD is in KNOWN_CIC_PROFILES, returns the designated profile.
    Otherwise, deterministically hashes the CCCD to generate a consistent, realistic report.
    """
    clean_id = re.sub(r"\D", "", str(national_id))
    if len(clean_id) < 9:
        clean_id = clean_id.zfill(12)
    elif len(clean_id) > 12:
        clean_id = clean_id[:12]

    now_iso = datetime.now(timezone.utc).isoformat()

    if clean_id in KNOWN_CIC_PROFILES:
        p = KNOWN_CIC_PROFILES[clean_id]
        group = p["bad_debt_group"]
        score = p["cic_score"]
        return CICReport(
            national_id=clean_id,
            query_timestamp=now_iso,
            status="SUCCESS",
            cic_score=score,
            cic_grade=_map_grade_from_score(score),
            bad_debt_group=group,
            bad_debt_group_name=GROUP_NAMES.get(group, "Nhóm 1: Nợ đủ tiêu chuẩn"),
            active_institutions_count=p["active_institutions_count"],
            total_recorded_debt=p["total_recorded_debt"],
            overdue_days_max=p["overdue_days_max"],
            legal_warning=p.get("legal_warning"),
        )

    # Deterministic generation based on hash of national_id
    h = int(hashlib.md5(clean_id.encode("utf-8")).hexdigest()[:8], 16)
    score = 450 + (h % 380)  # 450 to 830
    if score >= 680:
        group = 1
        overdue = 0
        lenders = (h % 2) + 1
        debt = float((h % 40) * 1_000_000)
        warning = None
    elif score >= 580:
        group = 2
        overdue = 15 + (h % 45)
        lenders = (h % 3) + 1
        debt = float(10_000_000 + (h % 50) * 1_000_000)
        warning = "Có lịch sử chậm thanh toán trong 12 tháng qua"
    elif score >= 500:
        group = 3
        overdue = 95 + (h % 60)
        lenders = (h % 4) + 2
        debt = float(30_000_000 + (h % 80) * 1_000_000)
        warning = "Cảnh báo nợ xấu Nhóm 3 tại TCTD khác"
    else:
        group = 4 if (h % 2 == 0) else 5
        overdue = 200 + (h % 200)
        lenders = (h % 5) + 2
        debt = float(80_000_000 + (h % 150) * 1_000_000)
        warning = f"Cảnh báo nghiêm trọng: Nợ xấu {GROUP_NAMES.get(group)}"

    return CICReport(
        national_id=clean_id,
        query_timestamp=now_iso,
        status="SUCCESS",
        cic_score=score,
        cic_grade=_map_grade_from_score(score),
        bad_debt_group=group,
        bad_debt_group_name=GROUP_NAMES.get(group, "Nhóm 1: Nợ đủ tiêu chuẩn"),
        active_institutions_count=lenders,
        total_recorded_debt=debt,
        overdue_days_max=overdue,
        legal_warning=warning,
    )


def cross_validate_with_cic(
    customer_data: Dict[str, Any],
    cic_report: CICReport,
) -> Tuple[List[str], List[str]]:
    """Cross-validate self-declared customer payload against official CIC registry.

    Returns:
        (fraud_flags, policy_violations)
    """
    fraud_flags: List[str] = []
    policy_violations: List[str] = []

    declared_defaults = int(customer_data.get("previous_defaults", 0))
    declared_debt = float(customer_data.get("existing_debt", 0.0))

    # 1. Bad debt misrepresentation detection
    if cic_report.bad_debt_group >= 3 and declared_defaults == 0:
        fraud_flags.append(
            f"cic_misrepresentation_detected: Customer declared 0 defaults, "
            f"but CIC records Group {cic_report.bad_debt_group} bad debt "
            f"({cic_report.overdue_days_max} days overdue)."
        )

    # 2. Severe bad debt policy violation
    if cic_report.bad_debt_group >= 3:
        policy_violations.append(
            f"policy_cic_bad_debt_group_{cic_report.bad_debt_group}: "
            f"Applicant has historical {cic_report.bad_debt_group_name} "
            f"under SBV Circular regulations — mandatory rejection/review."
        )

    # 3. Debt under-reporting detection
    # If self-declared debt is less than half of official CIC debt (and difference > 15M VND)
    if cic_report.total_recorded_debt > 15_000_000.0:
        if declared_debt < 0.5 * cic_report.total_recorded_debt:
            fraud_flags.append(
                f"cic_debt_underreporting_detected: Self-declared debt ({declared_debt:,.0f} VND) "
                f"is significantly lower than official CIC balance ({cic_report.total_recorded_debt:,.0f} VND)."
            )

    # 4. Multi-institution credit stress
    if cic_report.active_institutions_count >= 4:
        policy_violations.append(
            f"policy_multi_banking_indebtedness: Applicant currently holds loans "
            f"at {cic_report.active_institutions_count} different financial institutions."
        )

    return fraud_flags, policy_violations
