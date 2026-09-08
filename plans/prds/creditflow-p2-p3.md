# PRD — CreditFlow P2+P3: EDA + Feature Engineering (Colab-ready, không train local)

> **Slice**: P2 + P3 / 11 phases (spec §6, §7)
> **Spec source**: `docs/spec.md` v0.1 | **P1**: `plans/prds/creditflow-p1.md` + `docs/architecture/domains/credit-data-dictionary.md`
> **Status**: Approved — triển khai theo plan đã duyệt
> **Nguyên tắc**: Code Colab-ready, owner chạy trên Colab qua extension. Agent không chạy training, không dùng GPU local. Chỉ verify syntax.

---

## 1. Goals

* [ ] EDA skeleton trả lời Q1 (default có đặc điểm gì), Q2 (feature nào liên quan mạnh), Q3 (imbalance có nghiêm trọng không) + leakage investigation riêng.
* [ ] Feature code pure Pandas cho 5 derived features + guard chia-cho-0 + unit test biên.
* [ ] Mọi output EDA in `P(default)` toàn cục + từng split.

## 2. Non-goals

* Không train model (P4), không tune threshold (P5), không MLflow (P7).
* Không tải full Home Credit về local. Dữ liệu chỉ mở trên Colab.

## 3. Deliverables (khóa ở slice này)

1. `notebooks/creditflow_eda_p2_colab.py` — EDA skeleton, chạy trên Colab, sections Q1/Q2/Q3/leakage.
2. `pipeline/validation/schemas.py` — canonical schema + `validate_dataframe()` (pure pandas).
3. `pipeline/feature_engineering/features.py` — `add_derived_features()` + flags.
4. `tests/test_features.py` — unit test chia-cho-0, biên `age=18`, outlier flag.
5. `requirements-colab.txt` — pandas/numpy/matplotlib/seaborn/scikit-learn (phiên bản chốt khi owner chạy lần đầu).

## 4. Acceptance criteria

* [ ] EDA skeleton có đủ 4 sections + in imbalance + leakage suspects (`previous_defaults`, time-based columns).
* [ ] `add_derived_features()` khớp công thức data-dictionary v0.1, không sinh `inf`, mọi vi phạm ra `NaN + flag`.
* [ ] `python -m compileall` pass cho 3 file pipeline/tests. Không cần chạy pytest có dữ liệu thật ở slice này.
* [ ] Không có code `.fit()`, không import xgboost/torch ở slice này.

## 5. Task Breakdown

* [x] PRD này.
* [ ] `pipeline/validation/schemas.py`.
* [ ] `pipeline/feature_engineering/features.py`.
* [ ] `tests/test_features.py` + `requirements-colab.txt`.
* [ ] `notebooks/creditflow_eda_p2_colab.py`.
* [ ] Verify `compileall` + báo owner chạy Colab.

## 6. Risks

* Proxy thiếu cột (nhất là `existing_debt`, `loan_term`) → EDA ghi rõ missing-rate, không bịa số. Mapping tradeoff ghi vào report.
* `previous_defaults` leakage → EDA phải tách mục riêng, P4 không được dùng nếu P2 kết luận leak.
