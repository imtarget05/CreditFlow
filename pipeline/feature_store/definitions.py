"""
Feature definitions — hợp đồng duy nhất cho một feature.

Điểm mấu chốt của feature store là **train/serve parity**: cùng một định nghĩa
phải chạy giống nhau lúc huấn luyện và lúc phục vụ. Nếu hai bên tự viết logic
riêng thì parity chỉ là hy vọng, không phải bảo đảm.

Vì vậy ở đây chỉ có MỘT nơi định nghĩa cách tính feature. Store chỉ lo lưu
trữ và truy vấn theo thời gian — không được biết gì về ngữ nghĩa nghiệp vụ.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

# Timestamp chuẩn: naive-UTC. Có timezone thì cũng chấp nhận, sẽ được chuẩn hoá.
EventTime = datetime


def to_utc(ts: Any) -> EventTime:
    """Chuẩn hoá timestamp về naive-UTC để mọi phép so sánh luôn đúng."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


@dataclass(frozen=True)
class FeatureValue:
    """Một feature tại một thời điểm, kèm thời điểm nó được tạo ra."""

    name: str
    value: Any
    event_time: EventTime
    entity_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_time", to_utc(self.event_time))


@dataclass(frozen=True)
class FeatureDefinition:
    """
    Một feature: tên, cách tính, dependency, TTL.

    `fn` nhận (entity_id, as_of, history) và trả về giá trị. `history` là
    list[FeatureValue] đã sắp theo event_time tăng dần VÀ đã lọc theo as_of —
    nghĩa là `fn` không bao giờ nhìn thấy tương lai.

    `fn` phải thuần khiết với (entity_id, as_of, history): cùng đầu vào luôn
    cho cùng kết quả. Đây là điều kiện để offline và online khớp nhau.
    """

    name: str
    fn: Callable[[str, EventTime, list], Any]
    depends_on: tuple = ()
    ttl_seconds: int | None = None
    description: str = ""
    dtype: str = "float"

    def compute(self, entity_id: str, as_of: EventTime,
                history: list) -> FeatureValue:
        as_of = to_utc(as_of)
        return FeatureValue(
            name=self.name,
            value=self.fn(entity_id, as_of, history),
            event_time=as_of,
            entity_id=entity_id,
        )


# ---------------------------------------------------------------------------
# Thư viện feature cơ bản cho tín dụng.
# Mỗi hàm tuân thủ đúng hợp đồng (entity_id, as_of, history).
# ---------------------------------------------------------------------------

#: Khoản vay được phát hành — nguồn cho mọi số liệu cửa sổ.
LOAN_ISSUED = "loan_issued"
#: Khoản vay bị default.
LOAN_DEFAULTED = "loan_defaulted"
#: Thu nhập tháng được báo cáo.
INCOME_REPORTED = "income_reported"

#: Tập sự kiện NGUỒN. Đây không phải feature — không có định nghĩa, không được
#: materialize trực tiếp. Chúng là dữ liệu thô mà feature dẫn xuất từ đó,
#: nên store phải giữ được chúng dù không tính được gì từ chúng.
RAW_EVENTS = frozenset({LOAN_ISSUED, LOAN_DEFAULTED, INCOME_REPORTED})

INCOME_WINDOW_DAYS = 90
DEFAULT_WINDOW_DAYS = 730


def _count(history: list, name: str) -> int:
    return int(sum(1 for h in history if h.name == name))


def _window_count(history: list, as_of: EventTime, name: str, days: int) -> int:
    """Số lần xảy ra trong cửa sổ `days` ngày tính lùi từ as_of."""
    if not days:
        return 0
    cutoff = as_of.timestamp() - days * 86400
    return int(sum(1 for h in history if h.name == name
                   and h.event_time.timestamp() >= cutoff))


def _window_sum(history: list, as_of: EventTime, name: str, days: int) -> float:
    """Tổng giá trị trong cửa sổ `days` ngày tính lùi từ as_of."""
    if not days:
        return 0.0
    cutoff = as_of.timestamp() - days * 86400
    return float(sum(h.value for h in history if h.name == name
                     and h.event_time.timestamp() >= cutoff) or 0.0)


def f_total_loans(entity_id: str, as_of: EventTime, history: list) -> int:
    """Tổng số khoản vay đã phát hành — tính đến as_of, không tính tương lai."""
    return _count(history, LOAN_ISSUED)


def f_default_count_2y(entity_id: str, as_of: EventTime, history: list) -> int:
    return _window_count(history, as_of, LOAN_DEFAULTED, DEFAULT_WINDOW_DAYS)


def f_income_90d(entity_id: str, as_of: EventTime, history: list) -> float:
    return _window_sum(history, as_of, INCOME_REPORTED, INCOME_WINDOW_DAYS)


def f_income_mean_90d(entity_id: str, as_of: EventTime, history: list) -> float:
    n = _window_count(history, as_of, INCOME_REPORTED, INCOME_WINDOW_DAYS)
    if n == 0:
        return 0.0
    return f_income_90d(entity_id, as_of, history) / n


def f_debt_to_income(entity_id: str, as_of: EventTime, history: list) -> float:
    """Tỷ lệ nợ / thu nhập. Thu nhập bằng 0 thì trả 0 — không chia cho 0."""
    income = f_income_90d(entity_id, as_of, history)
    if income <= 0:
        return 0.0
    return round(float(f_total_loans(entity_id, as_of, history)) / income, 6)


def f_default_rate(entity_id: str, as_of: EventTime, history: list) -> float:
    total = f_total_loans(entity_id, as_of, history)
    if total == 0:
        return 0.0
    return round(f_default_count_2y(entity_id, as_of, history) / total, 6)


def f_recency_days(entity_id: str, as_of: EventTime, history: list) -> float:
    """Số ngày từ khoản vay gần nhất. Chưa có thì -1, KHÔNG phải 0."""
    loans = [h for h in history if h.name == LOAN_ISSUED]
    if not loans:
        return -1.0
    delta = (as_of - loans[-1].event_time).total_seconds() / 86400
    return round(max(delta, 0.0), 4)


def f_has_income_history(entity_id: str, as_of: EventTime, history: list) -> float:
    """
    1 nếu có lịch sử thu nhập, 0 nếu không — phải phân biệt được với
    "có thu nhập nhưng bằng 0".

    Vì vậy đếm SỐ LẦN xuất hiện, không dựa vào tổng: tổng = 0 không nói được
    là không có dữ liệu hay có dữ liệu bằng 0.
    """
    return 1.0 if _window_count(history, as_of, INCOME_REPORTED,
                                INCOME_WINDOW_DAYS) > 0 else 0.0


#: Định nghĩa chuẩn. Thứ tự là thứ tự cột khi train. Đổi thứ tự là breaking change.
STANDARD_FEATURES: tuple = (
    FeatureDefinition("total_loans", f_total_loans, (LOAN_ISSUED,), None,
                      "Tong so khoan vay da phat hanh"),
    FeatureDefinition("default_count_2y", f_default_count_2y, (LOAN_DEFAULTED,),
                      90 * 86400, "So lan default trong 2 nam"),
    FeatureDefinition("income_90d", f_income_90d, (INCOME_REPORTED,), None,
                      "Tong thu nhap 90 ngay"),
    FeatureDefinition("income_mean_90d", f_income_mean_90d, (INCOME_REPORTED,), None,
                      "Thu nhap trung binh moi thang trong 90 ngay"),
    FeatureDefinition("debt_to_income", f_debt_to_income,
                      (LOAN_ISSUED, INCOME_REPORTED), None,
                      "Ty le tong khoan vay / thu nhap 90 ngay"),
    FeatureDefinition("default_rate", f_default_rate,
                      (LOAN_ISSUED, LOAN_DEFAULTED), None, "Ty le default"),
    FeatureDefinition("recency_days", f_recency_days, (LOAN_ISSUED,), 365 * 86400,
                      "Ngay tu khoan vay gan nhat, -1 neu chua co"),
    FeatureDefinition("has_income_history", f_has_income_history,
                      (INCOME_REPORTED,), None,
                      "1 neu co lich su thu nhap, 0 neu khong"),
)

FEATURE_BY_NAME = {f.name: f for f in STANDARD_FEATURES}

#: Tên được phép ghi thẳng vào store: feature đã định nghĩa + sự kiện nguồn.
#: Đặt sau FEATURE_BY_NAME vì cần hợp nhất hai tập đó.
WRITABLE_NAMES = frozenset(FEATURE_BY_NAME) | RAW_EVENTS

#: Thứ tự cột — dùng giống nhau lúc train và lúc phục vụ.
FEATURE_ORDER: tuple = tuple(f.name for f in STANDARD_FEATURES)


def feature_names() -> list:
    return list(FEATURE_ORDER)


def get_feature(name: str) -> FeatureDefinition:
    if name not in FEATURE_BY_NAME:
        raise KeyError(
            f"feature '{name}' chua dinh nghia. Co: {sorted(FEATURE_BY_NAME)}")
    return FEATURE_BY_NAME[name]
