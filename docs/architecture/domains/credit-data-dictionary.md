# Data Dictionary — CreditFlow v0.1

> **Spec source**: `docs/spec.md` §5 + §7 | **PRD**: `plans/prds/creditflow-p1.md`
> **Phạm vi**: Canonical schema duy nhất. Mọi proxy dataset phải map về schema này. Không thêm feature ngoài dictionary nếu chưa qua spec revision.
> **Lưu ý mô phỏng**: schema này mô phỏng hồ sơ vay, không phải dữ liệu ngân hàng thật.

---

## 1. Raw features (canonical, tối thiểu)

| # | Name | Type | Unit | Range / Rule | Nullable | Mô tả |
|---|---|---|---|---|---|---|
| 1 | `income` | float | VND/tháng (contract) → serving chia 1000 về training-scale tại `backend/predict_service.py:to_model_units` (scale-alignment, không phải tỉ giá) | `> 0` | No | Thu nhập tại thời điểm nộp hồ sơ |
| 2 | `age` | int | năm | `18–100` | No | Tuổi tại thời điểm nộp hồ sơ |
| 3 | `employment_years` | float | năm | `>= 0`, `<= age - 18` (sanity) | No | Số năm làm việc liên tục |
| 4 | `loan_amount` | float | VND (contract) → serving chia 1000 như `income` | `> 0` | No | Số tiền vay đề nghị |
| 5 | `loan_term` | int | tháng | `> 0` (ví dụ 6–84, chốt theo dataset) | No | Kỳ hạn vay |
| 6 | `existing_debt` | float | VND (contract) → serving chia 1000 như `income` | `>= 0` | No | Tổng dư nợ hiện hữu |
| 7 | `credit_history` | float | năm | `>= 0`, `<= age - 18` (sanity) | No | Độ dài lịch sử tín dụng |
| 8 | `previous_defaults` | int | count | `>= 0` | No | Số lần default/vỡ nợ trước đây |

**Validation (enforced pipeline + API):** `income > 0`, `loan_amount > 0`, `age` trong range, các count/amount còn lại `>= 0`. Mọi vi phạm → reject ở validation layer + log, không impute thầm.

## 2. Target

| Name | Type | Định nghĩa |
|---|---|---|
| `default` | int {0,1} | `1` = default theo định nghĩa proxy dataset (`Home Credit TARGET=1` / `German bad=1`); `0` = còn lại. Chi tiết mapping theo dataset chốt ở P1. |

Báo cáo bắt buộc: `P(default)` toàn cục + từng split (train/val/test).

## 3. Derived features (công thức khóa, P3 implement)

| Name | Formula | Edge cases | Ví dụ |
|---|---|---|---|
| `debt_to_income` | `existing_debt / income` | `income > 0` đã validate; guard chia-cho-0 → `NaN + flag`, không để `inf` | 3500/2500 = 1.4 |
| `loan_to_income` | `loan_amount / income` | như trên | 12000/2500 = 4.8 |
| `debt_to_loan` | `existing_debt / loan_amount` | `loan_amount > 0` đã validate; guard tương tự | 3500/12000 ≈ 0.29 |
| `employment_stability` | hàm của `employment_years` (định nghĩa: `min(employment_years / 10, 1.0)`, chốt ngưỡng ở P3) | `employment_years` vượt `age-18` → flag outlier | 4 năm → 0.4 |
| `credit_history_year_ratio` | `credit_history / max(age - 18, 1)` | mẫu số `<= 0` → `0`; `credit_history > age-18` → flag | 5/(32-18) ≈ 0.36 |

Mọi derived feature phải có unit test: chia-cho-0, giá trị biên (`income` min, `age=18`), outlier flag.

## 4. Split

* **70 / 15 / 15** train/val/test, stratified theo `default`, `random_state=42`.
* Split **trước** fit preprocessing. Ghi `dataset_name + hash + mapping_version + seed` vào experiment log (chuẩn bị MLflow P7).

## 5. Proxy mapping (điền khi owner chốt dataset)

| Canonical | Home Credit (cột gốc) | German (cột gốc) | Ghi chú drop/transform |
|---|---|---|---|
| `income` | `AMT_INCOME_TOTAL` | `credit_amount`-derived | _điền ở P1 review_ |
| `age` | `DAYS_BIRTH → năm` | `age` | — |
| `employment_years` | `DAYS_EMPLOYED → năm` | `employment_duration` (map ordinal) | — |
| `loan_amount` | `AMT_CREDIT` | `credit_amount` | — |
| `loan_term` | suy từ annuity/term | `duration` | cần công thức rõ |
| `existing_debt` | `AMT_ANNUITY` + bureau agg | — | rủi ro thiếu → ghi tradeoff |
| `credit_history` | `DAYS_REGISTRATION / bureau` | `credit_history` (map ordinal) | — |
| `previous_defaults` | bureau `CREDIT_DAY_OVERDUE` agg | `credit_history=critical` | leakage suspect → P2 điều tra |

## 6. Versioning

* Dictionary version: `v0.1` (khóa cùng spec v0.1). Mọi thêm/sửa feature → bump version + spec revision, không sửa thầm (no steady-state compatibility).
