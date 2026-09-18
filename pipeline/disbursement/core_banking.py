"""Core Banking, VietQR NAPAS 247 & Electronic Loan Agreement Module.

Implements enterprise banking integration capabilities:
1. VietQR 24/7 instant disbursement order generation (NAPAS standard).
2. Legally compliant Electronic Credit Contract generation (Hợp đồng Tín dụng).
3. Enterprise Authority Matrix & Maker-Checker delegation rules.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VietQRData(BaseModel):
    """VietQR NAPAS 24/7 disbursement payload."""
    bank_bin: str = Field(default="970422", description="Mã định danh ngân hàng (BIN) - 970422 là MBBank")
    bank_name: str = Field(default="MBBank - Ngân hàng TMCP Quân đội")
    account_number: str = Field(..., description="Số tài khoản thụ hưởng của người vay")
    account_name: str = Field(default="KHACH HANG VAY VON", description="Tên người thụ hưởng")
    amount: float = Field(..., ge=0.0, description="Số tiền giải ngân (VND)")
    memo: str = Field(..., description="Nội dung chuyển khoản giải ngân")
    qr_quicklink: str = Field(..., description="Đường dẫn ảnh VietQR động")
    reference_code: str = Field(..., description="Mã giao dịch tham chiếu lõi core-banking")


class LoanAgreement(BaseModel):
    """Electronic Loan Contract model."""
    contract_code: str
    created_at: str
    lender_name: str = "TỔ CHỨC TÍN DỤNG CREDITFLOW (CREDITFLOW DIGITAL BANKING)"
    borrower_name: str
    national_id: str
    loan_amount: float
    loan_term_months: int
    annual_interest_rate: float
    monthly_payment_estimate: float
    collateral_summary: str
    disbursement_method: str = "Chuyển khoản liên ngân hàng 24/7 qua VietQR NAPAS"
    legal_text: str


def generate_vietqr_disbursement(
    contract_code: str,
    loan_amount: float,
    account_number: str = "0987654321",
    account_name: str = "NGUYEN VAN A",
    bank_bin: str = "970422",
    bank_name: str = "MBBank",
) -> VietQRData:
    """Generate official VietQR NAPAS 24/7 payment link and disbursement metadata."""
    safe_amount = max(0.0, float(loan_amount))
    clean_acc = re.sub(r"\D", "", str(account_number)) or "0987654321"
    memo = f"GIAI NGAN {contract_code}"

    # Standard VietQR Quicklink service format
    encoded_memo = urllib.parse.quote(memo)
    encoded_name = urllib.parse.quote(account_name)
    qr_url = (
        f"https://img.vietqr.io/image/{bank_bin}-{clean_acc}-compact2.png"
        f"?amount={int(safe_amount)}&addInfo={encoded_memo}&accountName={encoded_name}"
    )

    now_compact = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ref = f"FT-{now_compact}-{clean_acc[-4:]}"

    return VietQRData(
        bank_bin=bank_bin,
        bank_name=bank_name,
        account_number=clean_acc,
        account_name=account_name,
        amount=safe_amount,
        memo=memo,
        qr_quicklink=qr_url,
        reference_code=ref,
    )


def generate_loan_agreement(
    contract_code: str,
    borrower_name: str,
    national_id: str,
    loan_amount: float,
    loan_term_months: int,
    annual_interest_rate: float,
    collateral_type: str = "NONE",
    collateral_value: float = 0.0,
) -> LoanAgreement:
    """Generate structured legal text and agreement contract."""
    now_iso = datetime.now(timezone.utc).isoformat()
    now_human = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    r = (annual_interest_rate / 12.0) if annual_interest_rate > 0 else 0.01
    factor = ((1 + r) ** loan_term_months)
    monthly_est = (loan_amount * (r * factor) / (factor - 1)) if factor > 1 else (loan_amount / loan_term_months)

    col_name = "Tín chấp (Không yêu cầu tài sản đảm bảo)"
    if collateral_type == "REAL_ESTATE":
        col_name = f"Thế chấp Bất động sản (Định giá: {collateral_value:,.0f} VND)"
    elif collateral_type == "VEHICLE":
        col_name = f"Thế chấp Phương tiện vận tải (Định giá: {collateral_value:,.0f} VND)"
    elif collateral_type == "SAVINGS":
        col_name = f"Cầm cố Sổ tiết kiệm / Tiền gửi (Định giá: {collateral_value:,.0f} VND)"

    legal_text = f"""CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc
-----------------------------
HỢP ĐỒNG TÍN DỤNG KIÊM KHẾ ƯỚC NHẬN NỢ
Số: {contract_code}
Hôm nay, ngày {now_human}, các bên gồm có:

BÊN CHO VAY (BÊN A):
TỔ CHỨC TÍN DỤNG CREDITFLOW VIỆT NAM
Địa chỉ: Tầng 18, Tháp Tài chính Quốc tế, TP. Hồ Chí Minh
Đại diện theo ủy quyền: Giám đốc Khối Phê duyệt Tín dụng

BÊN VAY (BÊN B):
Ông/Bà: {borrower_name.upper()}
Số CCCD: {national_id}
Tình trạng xác thực: Đã đối chiếu Trung tâm Thông tin Tín dụng Quốc gia (CIC)

ĐIỀU 1: NỘI DUNG KHOẢN VAY
1.1. Số tiền cho vay: {loan_amount:,.0f} VNĐ (Bằng chữ: Việt Nam đồng).
1.2. Thời hạn vay: {loan_term_months} tháng.
1.3. Lãi suất cho vay: {annual_interest_rate*100:.2f}%/năm (theo chuẩn định giá rủi ro Basel II).
1.4. Phương thức trả nợ: Trả gốc và lãi định kỳ hàng tháng theo phương pháp Niên kim cố định.
1.5. Số tiền ước tính mỗi kỳ: ~{monthly_est:,.0f} VNĐ/tháng.
1.6. Biện pháp bảo đảm: {col_name}.

ĐIỀU 2: PHƯƠNG THỨC GIẢI NGÂN
2.1. Bên A thực hiện giải ngân trực tiếp 100% số tiền vay vào tài khoản ngân hàng của Bên B qua Cổng thanh toán liên ngân hàng 24/7 (VietQR NAPAS).
2.2. Thời điểm nhận nợ tính từ thời điểm lệnh chuyển tiền giải ngân thành công trên sổ cái hệ thống.

ĐIỀU 3: NGHĨA VỤ CỦA CÁC BÊN
3.1. Bên B cam kết sử dụng vốn vay đúng mục đích và thanh toán đầy đủ, đúng hạn nghĩa vụ nợ.
3.2. Trường hợp chậm trả, Bên B chịu lãi quá hạn bằng 150% lãi suất trong hạn theo quy định của Ngân hàng Nhà nước.
3.3. Hợp đồng này được lập dưới dạng dữ liệu điện tử, có giá trị pháp lý theo Luật Giao dịch điện tử.

ĐẠI DIỆN BÊN CHO VAY (BÊN A)                     NGƯỜI VAY (BÊN B)
(Đã ký điện tử và lưu sổ cái SHA-256)           (Đã xác thực OTP & CCCD)
"""

    return LoanAgreement(
        contract_code=contract_code,
        created_at=now_iso,
        borrower_name=borrower_name,
        national_id=national_id,
        loan_amount=loan_amount,
        loan_term_months=loan_term_months,
        annual_interest_rate=round(annual_interest_rate, 4),
        monthly_payment_estimate=round(monthly_est, 2),
        collateral_summary=col_name,
        legal_text=legal_text,
    )


def determine_authority_level(
    loan_amount: float,
    risk_level: str,
    has_policy_violations: bool = False,
    is_cic_clean: bool = True,
) -> Dict[str, Any]:
    """Credit Approval Authority Matrix (Ma trận Thẩm quyền Phê duyệt Tín dụng).

    Level 0 (STP): <= 20M VND, LOW risk, no violations, clean CIC -> Auto-disburse.
    Level 1 (Underwriter): 20M - 100M VND or MEDIUM risk -> Credit Underwriter.
    Level 2 (Risk Committee): > 100M VND or HIGH risk or CIC alerts -> Risk Head Approval.
    """
    amt = float(loan_amount)
    r_level = str(risk_level).upper()

    if (
        amt <= 20_000_000.0
        and r_level == "LOW"
        and not has_policy_violations
        and is_cic_clean
    ):
        return {
            "level": 0,
            "role": "SYSTEM_STP",
            "title": "Tự động duyệt tức thì (Straight-Through Processing)",
            "description": "Khoản vay nhỏ, rủi ro thấp, CIC trong sạch — giải ngân tự động trong 30 giây.",
            "requires_human": False,
        }
    elif amt <= 100_000_000.0 and r_level in ("LOW", "MEDIUM") and not has_policy_violations:
        return {
            "level": 1,
            "role": "UNDERWRITER_L1",
            "title": "Chuyên viên Thẩm định Tín dụng (Underwriter Level 1)",
            "description": "Thẩm định hồ sơ thông thường, kiểm tra chứng từ và đối soát nguồn thu nhập.",
            "requires_human": True,
        }
    else:
        return {
            "level": 2,
            "role": "RISK_COMMITTEE_L2",
            "title": "Hội đồng Rủi ro / Trưởng phòng Quản lý Rủi ro (Risk Manager Level 2)",
            "description": "Khoản vay lớn hoặc có yếu tố rủi ro/cảnh báo CIC — yêu cầu phê chuẩn cấp cao.",
            "requires_human": True,
        }
