# Product Spec: CreditFlow — ML Risk Decision Support System

> **Status**: Approved Draft (product truth)
> **Version**: v0.1 — 2026-09-08
> **Nguồn**: Đề xuất project #3 của owner (Decision Support System cho doanh nghiệp tài chính)
> **Nguyên tắc**: `docs/spec.md` là source of truth duy nhất. Mọi PRD/sprint/tasks suy ra từ đây.

---

## 1. Product intent

CreditFlow **không phải** là "Credit Score Prediction kiểu notebook". CreditFlow là một **Production ML Credit Risk Decision Engine**: mô phỏng pipeline xử lý hàng nghìn hồ sơ vay/credit mỗi ngày, tự động sàng lọc rủi ro nhanh hơn, nhất quán hơn, giảm bỏ sót khách hàng rủi ro cao, đồng thời **không phải black box** (audit được model + experiment + decision).

Pipeline chuẩn:

```
Loan Application → Data Validation → Data Cleaning → EDA
→ Feature Engineering → ML Training (Logistic / Tree / RandomForest / XGBoost)
→ Model Evaluation → Best Model → MLflow Registry
→ FastAPI → Docker → Cloud Deployment → Monitoring
```

### Non-goals (nói rõ để tránh scope creep)

* Không claim đã triển khai credit scoring cho ngân hàng thật. Dataset công khai chỉ là **proxy** cho bài toán doanh nghiệp.
* Không tối ưu Accuracy đơn thuần.
* Không deploy Frontend React ở slice đầu. Frontend là tầng optional sau khi Backend ổn định.
* Không training nặng ở local. Training thực hiện **trên Colab qua extension**, do owner điều khiển.

## 2. Users

* **Risk analyst**: cần decision + reasons giải thích được, truy vết model version.
* **Engineering / hiring reviewer (recruiter)**: cần thấy evidence tabular ML + data engineering + classical ML + production ML pipeline.
* **Owner (portfolio)**: CreditFlow lấp lỗ hổng portfolio — MAIA (AI/RAG apps), Hermes (agents/orchestration), CreditFlow (classical ML productionized).

## 3. Business problem

### Hiện trạng (giả định)

```
Customer → Submit application → Analyst reviews data
→ Risk assessment → Approve / Reject
```

Khi scale: xử lý thủ công chậm, tiêu chí không nhất quán, khó phát hiện hồ sơ rủi ro, dữ liệu drift theo thời gian, model cũ xuống chất lượng mà doanh nghiệp không biết, chỉ nhìn Accuracy thì sai lầm, thiếu audit trail.

### Business objective (chuẩn spec)

> **Tối ưu khả năng phát hiện khách hàng có nguy cơ default trong khi kiểm soát tỷ lệ từ chối nhầm khách hàng tốt.**

Hệ quả trực tiếp: chọn model và threshold dựa trên **Recall / F1 + business cost**, không dựa trên Accuracy.

## 4. System outputs (Decision Service, không chỉ ML model)

### 4.1 Request

```json
{
  "income": 2500,
  "age": 32,
  "employment_years": 4,
  "loan_amount": 12000,
  "loan_term": 36,
  "existing_debt": 3500,
  "credit_history": 5,
  "previous_defaults": 0
}
```

### 4.2 Response tối thiểu

```json
{
  "risk_probability": 0.78,
  "risk_level": "HIGH",
  "decision": "REVIEW",
  "model_version": "xgboost-v12"
}
```

### 4.3 Response mục tiêu (có explainability)

```json
{
  "decision": "REVIEW",
  "risk_probability": 0.78,
  "model_version": "xgboost-v12",
  "reasons": [
    "high debt-to-income ratio",
    "short credit history",
    "high loan-to-income ratio"
  ]
}
```

`reasons` ở slice đầu có thể là rule-based từ engineered features; về sau thay bằng SHAP/feature-importance nhưng contract response giữ nguyên.

### 4.4 Decision thresholds (twist — Decision Engineering)

Mặc định đề xuất, hiệu chỉnh sau bằng experiment + business cost:

* `probability > 0.80` → `REJECT`
* `0.50 – 0.80` → `REVIEW`
* `< 0.50` → `APPROVE`

Giả định cost: **False Negative (bỏ sót default) thiệt hại lớn hơn False Positive (từ chối nhầm khách tốt)**. Spec yêu cầu mọi thay đổi threshold phải ghi lý do cost và đi kèm confusion matrix.

## 5. Data layer

Stack: **Python + Pandas + NumPy**. Bắt buộc xây data pipeline, không load CSV rồi `.fit()`.

```
Raw Data → Pandas ingestion → Schema validation → Missing-value analysis
→ Outlier analysis → Duplicate detection → Cleaning → Feature dataset
```

### 5.1 Raw features (tối thiểu)

`income, age, employment_years, loan_amount, loan_term, existing_debt, credit_history, previous_defaults`

### 5.2 Validation rules (enforced ở cả pipeline và API)

* `income > 0`, `loan_amount > 0`
* `age` trong range hợp lệ (ví dụ 18–100, chốt ở data dictionary)
* `employment_years >= 0`, `existing_debt >= 0`, `previous_defaults >= 0`
* Schema, missing, outlier, duplicate đều phải có báo cáo.

## 6. EDA — phải trả lời câu hỏi business

Không vẽ biểu đồ cho đẹp. EDA phải trả lời:

* Q1: Khách hàng default có đặc điểm gì?
* Q2: Feature nào liên quan mạnh tới default?
* Q3: Class imbalance có nghiêm trọng không? (ví dụ Default 12% / Non-default 88% → Accuracy 88% gần như vô nghĩa nếu model luôn đoán non-default).

Deliverable EDA: missing values, distributions, correlations, class imbalance, suspicious features, **leakage investigation**. Core: Pandas, NumPy, Matplotlib/Seaborn.

## 7. Feature Engineering

Từ raw → derived → final feature set. Tối thiểu:

* `debt_to_income = existing_debt / income`
* `loan_to_income = loan_amount / income`
* `debt_to_loan = existing_debt / loan_amount`
* `employment_stability` (hàm của `employment_years`, định nghĩa ở data dictionary)
* `credit_history_year_ratio` (hàm của `credit_history` và `age`, định nghĩa ở data dictionary)

Mọi công thức phải có trong data dictionary + unit test cho chia-cho-0 và giá trị biên. Core: Pandas, NumPy, scikit-learn.

## 8. Model experimentation (benchmark, không mặc định winner)

Bắt buộc train và so sánh 4 models:

1. **Logistic Regression** — baseline, đơn giản, dễ giải thích.
2. **Decision Tree** — kiểm tra nonlinear relationship, dễ diễn giải.
3. **Random Forest** — giảm overfitting của single tree, ensemble, feature importance.
4. **XGBoost** — candidate production, nhưng **không mặc định thắng. Experiment quyết định.**

## 9. Model evaluation

Bảng so sánh bắt buộc (`Precision / Recall / F1 / Accuracy` + confusion matrix cho model được chọn):

| Model | Precision | Recall | F1 | Accuracy |
|---|---|---|---|---|
| Logistic Regression | 0.xx | 0.xx | 0.xx | 0.xx |
| Decision Tree | 0.xx | 0.xx | 0.xx | 0.xx |
| Random Forest | 0.xx | 0.xx | 0.xx | 0.xx |
| XGBoost | 0.xx | 0.xx | 0.xx | 0.xx |

Business interpretation bắt buộc:

* Precision thấp → nhiều khách bị đánh dấu risky oan.
* Recall thấp → bỏ sót nhiều khách default (nguy hiểm nhất).
* F1 → cân bằng Precision/Recall.
* Câu quyết định khi phỏng vấn: *"Production model không được chọn chỉ dựa trên Accuracy; chúng tôi ưu tiên Recall/F1 vì chi phí bỏ sót risky customers cao hơn."*

## 10. ML Pipeline (reproducible, tránh train/inference skew)

Không dùng `train.py` đơn lẻ. Cấu trúc chuẩn:

```
pipeline/
├── ingestion
├── validation
├── preprocessing
├── feature_engineering
├── training
├── evaluation
└── registration
```

Luồng: `Raw Data → Preprocessing → Feature Engineering → Train/Test Split → Training → Evaluation → Model Selection → MLflow`. Bắt buộc dùng `sklearn.pipeline.Pipeline`:

```python
Pipeline([
    ("preprocessor", preprocessor),
    ("model", model),
])
```

## 11. MLflow (audit được)

Mỗi experiment lưu: `experiment_id, model, parameters, dataset_version, metrics, artifact, timestamp`. Ví dụ Experiment #17: `XGBoost, max_depth=6, lr=0.05, n_estimators=300 → P=0.71, R=0.83, F1=0.76`. Sau đó `v1 → v2 → v3 → v4 → Best candidate → Production`.

## 12. FastAPI (production API, không còn notebook)

Endpoints bắt buộc:

* `POST /predict` → `{risk_probability, risk_level, model_version (+ reasons)}`
* `GET /health`
* `GET /model/info` (model version đang serve)
* `GET /metrics` (cho monitoring)

Validation bằng Pydantic theo §5.2.

## 13. Docker

Inference service containerized: `FastAPI App + Model + Preprocessor + Prediction Service` trong 1 image. Local có thể tách `api / mlflow / monitoring` bằng Docker Compose.

## 14. Cloud deployment

Mục tiêu: `Internet → Cloud → FastAPI Container → ML Model`. Acceptance:

* `GET /health = 200`
* `POST /predict` trả prediction hợp lệ
* `model version` visible qua `/model/info`

## 15. Monitoring (vượt student level)

* **System**: request count, latency, error rate, CPU, memory. Luồng `API → Metrics → Prometheus → Dashboard`.
* **ML**:
  * Data drift (ví dụ train `avg_income=2500` vs production `4100`).
  * Prediction drift (ví dụ train `HIGH RISK=12%` vs production `31%`).
  * Performance degradation khi có delayed labels (`F1: 0.78 → 0.75 → 0.69 → 0.61` → trigger retraining investigation).

## 16. Architecture hoàn chỉnh (text)

```
Client/UI → FastAPI → Prediction Service → ML Model
Training side: Raw Data → Pandas/NumPy → EDA → Feature Engineering
→ sklearn Pipeline → [Logistic|Tree|RF|XGBoost] → Evaluation
→ MLflow Tracking → Registry → Production
Production: FastAPI → Docker → Cloud → Monitoring (System + Data Drift + Prediction Drift)
```

Slice đầu: chỉ Backend (FastAPI). Frontend React là optional, sau.

## 17. Roadmap thực hiện (11 phases, cắt lát)

* P1 Business + Data: problem statement, dataset, data dictionary, target definition, business cost assumptions, train/val/test split.
* P2 EDA: theo §6.
* P3 Feature Engineering: theo §7.
* P4 Model Benchmark: 4 models + comparison table.
* P5 Evaluation: metrics + confusion matrix + business justification.
* P6 ML Pipeline: reproducible pipeline §10.
* P7 MLflow: tracking + registry.
* P8 FastAPI: 4 endpoints §12.
* P9 Docker: containerize inference.
* P10 Cloud: deploy + acceptance §14.
* P11 Monitoring: API metrics + drift detection.

## 18. JD coverage (mỗi tech tồn tại vì requirement)

Python (toàn project), Pandas (ingestion/cleaning/EDA), NumPy (numeric), EDA (notebook/report), Feature Engineering (credit features), scikit-learn (preprocessing + models), Logistic/Tree/RF/XGBoost (benchmark), Precision/Recall/F1 (selection), ML Pipeline (reproducible), FastAPI (API), Docker (container), MLflow (tracking + registry), Cloud (deployment), Monitoring (API + ML).

## 19. Constraints & compliance (bắt buộc)

1. **Training trên Colab**: mọi training nặng chạy trên Colab qua extension do owner điều khiển. Agent **không tự ý dùng GPU local** chạy training khi chưa được cho phép. Agent chỉ chuẩn bị code Colab-ready + requirements + hướng dẫn chạy.
   - **Deviation (2026-09-09)**: For 5k-row synthetic dataset, training runs locally (`scripts/train_models.py`, seed 42, ~10s CPU). Colab path remains available via `notebooks/creditflow_model_benchmark.ipynb`. Owner may restore `requirements-colab.txt` and shift to Colab if dataset scales.
2. **Proxy dataset disclaimer**: dataset công khai chỉ là proxy. Không viết CV/log/report như thể đã triển khai cho ngân hàng thật. Mọi artifact phải ghi rõ mô phỏng.
3. **No steady-state compatibility**: không giữ path cũ khi spec này đã chốt; mọi thay đổi qua spec revision.
4. **Kỷ luật portfolio**: giữ vai trò bộ 3 — MAIA (AI/RAG), Hermes (agents/orchestration), CreditFlow (classical ML productionized).

## 20. Acceptance scenarios (Definition of Done cho spec này)

* [ ] Data dictionary định nghĩa đủ raw + derived features + target + split.
* [ ] API contract §4 + thresholds §4.4 được review chốt.
* [ ] Evaluation rubric §9 + cost assumption FN > FP được ghi nhận.
* [ ] Architecture §16 + roadmap §17 được dùng làm Grundlage cho PRD/sprint tiếp theo.
* [ ] Constraints §19 được mọi agent tuân thủ.
