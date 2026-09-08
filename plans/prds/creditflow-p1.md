# PRD — CreditFlow P1: Business + Data Foundation

> **Slice**: P1 / 11 phases (§17 spec)
> **Spec source**: `docs/spec.md` v0.1 (§3, §5, §6, §19, §20)
> **Status**: Draft → chờ owner review
> **Nguyên tắc**: Không training ở slice này. Không dùng GPU local. Chỉ chốt data truth + hợp đồng schema.

---

## 1. Problem statement (từ spec §3)

Tổ chức tài chính xử lý hàng nghìn hồ sơ vay/ngày bằng review thủ công: chậm, tiêu chí không nhất quán, bỏ sót hồ sơ rủi ro cao, dữ liệu drift mà không biết, chỉ nhìn Accuracy nên chọn sai model, thiếu audit trail.

P1 trả lời: *business problem là gì, dữ liệu nào mô phỏng nó, target nào đo được, cost nào dẫn dắt threshold, split nào cho experiment công bằng.*

## 2. Goals / Non-goals

**Goals (Definition of Done cho P1):**

* [ ] Chốt proxy dataset + mapping về canonical schema 8 features.
* [ ] Chốt data dictionary (`docs/architecture/domains/credit-data-dictionary.md`).
* [ ] Chốt target definition + business cost assumptions (FN > FP).
* [ ] Chốt train/val/test split + seed + stratification.
* [ ] Liệt kê leakage suspects + imbalance giả định để P2 kiểm chứng.

**Non-goals:**

* Không EDA sâu (đó là P2), không feature code (P3), không train model (P4).
* Không tải dataset về local chạy training. Owner chạy kiểm chứng trên Colab nếu muốn.

## 3. Proxy dataset (disclaimer bắt buộc)

> Dataset công khai chỉ là **proxy mô phỏng** bài toán doanh nghiệp. Không claim triển khai ngân hàng thật. Mọi artifact/CV phải ghi rõ mô phỏng.

**Đề xuất chính: Home Credit Default Risk (Kaggle) — sample/subset.**
Lý do: tabular credit thật, có income/loan/credit history/default flag, đủ lớn để demo imbalance + drift, chạy được trên Colab free.

**Fallback: German Credit Data (UCI).** Nhỏ, dễ review thủ công, hợp khi cần giải thích baseline Logistic.

**Mapping bắt buộc**: mọi cột dataset gốc phải map về canonical schema 8 features (§5.1 spec) qua 1 file mapping duy nhất (ví dụ `mapping.yaml` ở P3). Cột nào không map được → ghi rõ `dropped + lý do`, không tự ý nhét feature lạ vào model.

## 4. Target definition

* `default = 1` nếu hồ sơ vi phạm nghĩa vụ theo định nghĩa của proxy dataset (ví dụ Home Credit: `TARGET=1`; German: `bad credit = 1`). Mapping chi tiết trong data dictionary.
* `default = 0` ngược lại.
* Mọi notebook/report phải in `P(default)` toàn cục + theo split để lộ imbalance ngay từ đầu.

## 5. Business cost assumptions (dẫn dắt threshold §4.4)

* **FN (bỏ sót default) >> FP (từ chối nhầm khách tốt).** Số liệu minh họa sẽ chốt ở P5 sau khi có confusion matrix, nhưng P1 khóa nguyên tắc: *model/threshold được chọn bằng Recall/F1 + cost, không bằng Accuracy.*
* Thresholds mặc định kế thừa spec: `>0.80 REJECT / 0.50–0.80 REVIEW / <0.50 APPROVE`. Mọi thay đổi sau này phải kèm confusion matrix + lý do cost.

## 6. Train / Val / Test split

* Tỉ lệ: **70 / 15 / 15**, stratified theo `default`, `random_state=42`.
* Lý do: train đủ lớn cho 4 models benchmark; val để tune threshold cost-sensitive; test khóa 1 lần cho báo cáo P5.
* Dataset versioning: ghi `dataset_name + version/hash + mapping_version + split_seed` vào mọi experiment sau này (chuẩn bị cho MLflow P7).
* Không leak: split **trước** mọi bước fit preprocessing (P6 enforce bằng `sklearn Pipeline`).

## 7. Validation rules (khóa ở P1, enforce ở pipeline + API sau)

| Rule | Áp dụng |
|---|---|
| `income > 0`, `loan_amount > 0` | pipeline + Pydantic |
| `age` 18–100 | pipeline + Pydantic |
| `employment_years >= 0`, `existing_debt >= 0`, `previous_defaults >= 0` | pipeline + Pydantic |
| Schema + missing + outlier + duplicate báo cáo | P2 |

## 8. Leakage suspects (giao cho P2 điều tra)

* `previous_defaults` tương quan quá mạnh với target → kiểm tra có phải thông tin chỉ có sau khi default không.
* Mọi feature có hậu tố thời gian (ví dụ days-past-due tại thời điểm sau giải ngân) → cấm dùng làm input nếu không có tại thời điểm application.
* P2 phải có mục **leakage investigation** riêng, không gộp chung với correlation.

## 9. Deliverables

1. File này (PRD P1).
2. `docs/architecture/domains/credit-data-dictionary.md` — data dictionary chuẩn.
3. Quyết định dataset chính thức của owner (Home Credit subset vs German).

## 10. Acceptance criteria

* [ ] Data dictionary đủ raw + derived + target + split (spec §20).
* [ ] Owner chốt proxy dataset + mapping hướng.
* [ ] Cost assumption FN > FP được ghi nhận bằng văn bản (mục §5).
* [ ] Không có code training, không dùng GPU ở slice này.

## 11. Task Breakdown (execution checklist — cập nhật tại đây)

* [x] Viết PRD P1 (file này).
* [ ] Viết data dictionary v0.1.
* [ ] Owner review: chốt dataset + thresholds mặc định.
* [ ] Chuyển P2: EDA skeleton Colab-ready (chưa chạy local).

## 12. Risks

* Proxy dataset thiếu 1 trong 8 features → phải định nghĩa derived/mapped thay thế, ghi rõ tradeoff.
* Imbalance cực nặng (<5% default) → P4 cần class_weight/sampling, nhưng **không quyết ở P1**, chỉ ghi nhận.
* Drift giữa train/prod → P11 xử lý, P1 chỉ chuẩn bị dataset versioning.
