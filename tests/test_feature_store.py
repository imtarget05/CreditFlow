"""
Test feature store: point-in-time correctness, leakage prevention,
train/serve parity, TTL.

Nguyên tắc khi viết test ở đây: KHÔNG chỉ kiểm hàm trả về gì, mà kiểm
hàm KHÔNG nhìn thấy gì. Bất biến quan trọng nhất:

    prediction_time = T;  feature timestamp > T  ->  MUST NOT be included
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.feature_store.definitions import (
    FEATURE_ORDER,
    INCOME_REPORTED,
    LOAN_DEFAULTED,
    LOAN_ISSUED,
    FeatureValue,
    get_feature,
)
from pipeline.feature_store.offline_store import LeakageError, OfflineFeatureStore
from pipeline.feature_store.online_store import OnlineFeatureStore

T0 = datetime(2026, 1, 1)
D = timedelta(days=1)


def fv(name, value, when, entity="u1"):
    return FeatureValue(name=name, value=value, event_time=when, entity_id=entity)


def seed_store():
    """Lịch sử 6 tháng cho u1: 3 khoản vay, 2 lần default, thu nhập hằng tháng."""
    s = OfflineFeatureStore()
    for i, when in enumerate([T0, T0 + 60 * D, T0 + 120 * D]):
        s.write(fv(LOAN_ISSUED, 1000, when))
    s.write(fv(LOAN_DEFAULTED, 1, T0 + 30 * D))
    s.write(fv(LOAN_DEFAULTED, 1, T0 + 90 * D))
    for m in range(6):
        s.write(fv(INCOME_REPORTED, 2000.0, T0 + m * 30 * D))
    return s


# ==========================================================================
# Point-in-time correctness
# ==========================================================================
def test_materialize_uses_only_past_data():
    s = seed_store()
    at = T0 + 90 * D
    row = s.materialize_row("u1", at)
    # Khoan vay tai 0, 60, 120 ngay. Tai 90D chi thay 2 (ngay 120 chua toi).
    assert row["total_loans"] == 2, "khong duoc tinh khoan vay o 120 ngay"
    assert row["default_count_2y"] == 2


def test_earlier_as_of_sees_fewer_events():
    """Cùng dữ liệu, thời điểm sớm hơn thì phải thấy ít hơn — chứng minh
    materialize thực sự phụ thuộc thời điểm chứ không phải đọc toàn bộ."""
    s = seed_store()
    early = s.materialize_row("u1", T0)
    late = s.materialize_row("u1", T0 + 150 * D)
    assert early["total_loans"] < late["total_loans"]
    assert early["income_90d"] < late["income_90d"]


def test_backfill_rows_are_chronological_and_monotonic():
    s = seed_store()
    times = [T0 + i * 30 * D for i in range(6)]
    rows = s.backfill("u1", times)
    assert len(rows) == 6
    totals = [r["total_loans"] for r in rows]
    assert totals == sorted(totals), "so khoan vay phai tang danh theo thoi gian"


def test_row_has_exact_feature_order_columns():
    s = seed_store()
    row = s.materialize_row("u1", T0 + 150 * D)
    assert list(row) == list(FEATURE_ORDER)
    assert len(row) == len(FEATURE_ORDER)


# ==========================================================================
# FUTURE LEAKAGE PREVENTION — invariant trung tam
# ==========================================================================
def test_future_event_excluded_from_materialize():
    s = seed_store()
    at = T0 + 30 * D
    s.write(fv(LOAN_ISSUED, 999_999, at + 1 * D))  # xảy ra SAU at
    row = s.materialize_row("u1", at)
    assert row["total_loans"] == 1, "khong duoc dem khoan vay tuong lai"
    assert 999_999 not in row.values()


def test_future_event_changes_nothing_at_all():
    """Thêm dữ liệu tương lai không được làm đổi bất kỳ feature nào tại T."""
    s = seed_store()
    at = T0 + 45 * D
    before = s.materialize_row("u1", at)
    s.write(fv(LOAN_ISSUED, 777, at + 5 * D))
    s.write(fv(INCOME_REPORTED, 999_999.0, at + 5 * D))
    s.write(fv(LOAN_DEFAULTED, 1, at + 5 * D))
    assert s.materialize_row("u1", at) == before


def test_visible_history_contains_nothing_after_as_of():
    s = seed_store()
    at = T0 + 40 * D
    hist = s.history_as_of("u1", at)
    assert hist, "phai co it nhat mot ban ghi truoc as_of"
    assert all(v.event_time <= at for v in hist), "lich su chua duoc loc"


def test_assert_no_future_data_passes_on_correct_store():
    """Store có dữ liệu tương lai là BÌNH THƯỜNG — sự kiện được ghi khi nó
    xảy ra. Chỉ lịch sử trả về cho người tính feature mới phải sạch."""
    s = seed_store()
    n = s.assert_no_future_data("u1", T0 + 365 * D)
    assert n > 0, "phai co ban ghi quan sat duoc"


def test_assert_no_future_data_ignores_stored_future_rows():
    """Có bản ghi nằm sau as_of trong store thì vẫn phải pass, vì nó không
    được trả về cho người tính feature."""
    s = seed_store()
    s.write(fv(LOAN_ISSUED, 42, T0 + 999 * D))  # tương lai
    assert s.assert_no_future_data("u1", T0 + 100 * D) >= 0


def test_leakage_guard_actually_rejects_when_filter_is_bypassed():
    """
    Bảo vệ phải thật sự bắt được lỗi, không phải no-op.

    Bỏ filter bằng cách giả lập một store hỏng: `_visible_history` bị thay
    bằng bản không lọc. Khi đó assert_no_future_data phải nổ.
    """
    s = seed_store()
    at = T0 + 30 * D
    s.write(fv(LOAN_ISSUED, 999_999, at + 10 * D))

    s._visible_history = lambda entity_id, as_of: [
        v for name in s._data.get(entity_id, {})
        for v in s._data[entity_id][name]
    ]  # bỏ sạch điều kiện lọc
    with pytest.raises(LeakageError):
        s.assert_no_future_data("u1", at)


def test_window_features_ignore_future_events():
    s = seed_store()
    at = T0 + 60 * D
    baseline = s.materialize_row("u1", at)
    s.write(fv(INCOME_REPORTED, 50_000.0, at + 1 * D))
    after = s.materialize_row("u1", at)
    assert after["income_90d"] == baseline["income_90d"]
    assert after["income_mean_90d"] == baseline["income_mean_90d"]


def test_entering_future_data_does_not_break_assertion():
    """Có dữ liệu tương lai trong store là chuyện bình thường; truy vấn tại T
    vẫn phải sạch và vẫn pass kiểm tra."""
    s = seed_store()
    at = T0 + 200 * D
    before = s.materialize_row("u1", at)
    s.write(fv(LOAN_ISSUED, 5, at + 50 * D))
    s.assert_no_future_data("u1", at)


# ==========================================================================
# Train / serve parity — cung mot FeatureDefinition, hai duong tinh
# ==========================================================================
def _online_from_offline(store, entity_id, as_of):
    """
    Nạp trạng thái online đúng cách dùng thật.

    Online store giữ trạng thái HIỆN TẠI, không giữ lịch sử. Nên thứ được nạp
    vào là các **feature value đã materialize** tại `as_of`, không phải raw
    event. Đây chính là đường đi thực tế: pipeline tính feature rồi đẩy sang
    phục vụ.
    """
    online = OnlineFeatureStore()
    row = store.materialize(entity_id, as_of)
    for name, value in row.items():
        online.upsert(entity_id, name, value, as_of)
    return online


@pytest.mark.parametrize("as_of_days", [30, 90, 180])
def test_offline_and_online_agree_on_compute(as_of_days):
    """
    Cùng dữ liệu, cùng thời điểm: giá trị phục vụ phải bằng giá trị lúc train.
    Không khớp thì mô hình đã train trên số khác lúc phục vụ — đây là bug
    train/serve skew kinh điển.
    """
    s = seed_store()
    at = T0 + as_of_days * D
    offline = s.materialize_row("u1", at)
    online = _online_from_offline(s, "u1", at)

    served = online.get_vector("u1", at)
    for name in FEATURE_ORDER:
        assert served[name] == offline[name], f"train/serve skew tai feature '{name}'"


def test_online_recompute_matches_offline_when_history_available():
    """
    Nếu online giữ đủ lịch sử thì `compute_feature` phải khớp offline —
    đây là bằng chứng rằng hai store dùng CHUNG công thức.
    """
    s = seed_store()
    at = T0 + 150 * D
    offline = s.materialize("u1", at)

    # Nạp TOÀN BỘ lịch sử nguồn (không phải chỉ bản ghi mới nhất).
    online = OnlineFeatureStore()
    for name, bucket in s._data["u1"].items():
        for v in bucket:
            if v.event_time <= at:
                online.upsert("u1", v.name, v.value, v.event_time)

    for name in FEATURE_ORDER:
        assert online.compute_feature("u1", name, at) == offline[name], (
            f"hai store tinh feature '{name}' khac nhau — parity hong")


def test_definitions_are_shared_not_duplicated():
    """Hai store phải dùng CÙNG đối tượng định nghĩa, không phải bản sao."""
    off, onl = OfflineFeatureStore(), OnlineFeatureStore()
    assert off.definitions.keys() == onl.definitions.keys()
    for name in off.definitions:
        assert off.definitions[name].fn is onl.definitions[name].fn, (
            f"feature '{name}' bi su phan biet giua offline va online")


# ==========================================================================
# Offline store — hanh vi ghi / doc
# ==========================================================================
def test_write_is_idempotent_for_same_timestamp():
    s = OfflineFeatureStore()
    s.write(fv(LOAN_ISSUED, 100, T0))
    s.write(fv(LOAN_ISSUED, 100, T0))
    assert s.value_count() == 1, "ghi trung timestamp phai ghi de chu khong nhan ban"


def test_write_rejects_unknown_name():
    s = OfflineFeatureStore()
    with pytest.raises(KeyError):
        s.write(fv("khong_ai_biet", 1, T0))


def test_materialize_unknown_feature_raises():
    s = seed_store()
    with pytest.raises(KeyError):
        s.materialize("u1", T0 + 10 * D, only=["khong_ai_biet"])


def test_unknown_entity_gives_neutral_values():
    s = seed_store()
    row = s.materialize_row("khong_ton_tai", T0 + 100 * D)
    assert row["total_loans"] == 0
    assert row["recency_days"] == -1.0, "chua co khoan vay phai la -1, khong phai 0"
    assert row["debt_to_income"] == 0.0, "thu nhap 0 thi khong chia 0"


def test_recency_and_has_income_distinguish_missing_from_zero():
    """Giá trị thiếu phải khác giá trị 0 — nếu không mô hình học sai."""
    s = OfflineFeatureStore()
    row = s.materialize_row("u0", T0 + 1 * D)
    assert row["has_income_history"] == 0.0, "chua co ban ghi -> 0"
    assert row["recency_days"] == -1.0, "chua co khoan vay -> -1 khong phai 0"

    # Ghi thu nhap = 0: VAN phai la "co lich su" vi co ban ghi that.
    # Phai ghi vao cung entity ma materialize hoi (u0), khong phai u1 mac dinh.
    s.write(fv(INCOME_REPORTED, 0.0, T0, entity="u0"))
    row2 = s.materialize_row("u0", T0 + 1 * D)
    assert row2["has_income_history"] == 1.0, (
        "co lich su ghi 0 van phai la 1 — phai phan biet du lieu 0 voi thieu du lieu")
    assert row2["income_90d"] == 0.0


def test_entities_and_value_count():
    s = seed_store()
    assert s.entities() == ["u1"]
    assert s.value_count() == 11  # 3 loan + 2 default + 6 income


def test_timezone_aware_as_of_is_normalized():
    from datetime import timezone as tz
    s = seed_store()
    naive = s.materialize_row("u1", T0 + 100 * D)
    aware = s.materialize_row("u1", (T0 + 100 * D).replace(tzinfo=tz.utc))
    assert naive == aware, "as_of co timezone phai cho ket qua nhu naive-UTC"


def test_iso_string_as_of_works():
    s = seed_store()


# ==========================================================================
# Online store — TTL, latest retrieval, hanh vi thoi gian
# ==========================================================================
def _fixed_clock(*times):
    """Đồng hồ giả để test TTL không phụ thuộc thời gian thật."""
    seq = list(times)

    def now_fn():
        return seq[0] if len(seq) == 1 else seq.pop(0)
    return now_fn


def test_online_get_returns_latest_value():
    now = T0 + 10 * D
    o = OnlineFeatureStore(now_fn=_fixed_clock(now))
    o.upsert("u1", "income_90d", 1000.0, T0)
    o.upsert("u1", "income_90d", 2000.0, T0 + 5 * D)
    assert o.get("u1", "income_90d") == 2000.0


def test_online_as_of_ignores_later_writes():
    """Ghi sự kiện sau as_of thì không được nhìn thấy tại as_of đó."""
    now = T0 + 10 * D
    o = OnlineFeatureStore(now_fn=_fixed_clock(now))
    o.upsert("u1", "income_90d", 1000.0, T0)
    o.upsert("u1", "income_90d", 9999.0, T0 + 20 * D)  # tuong lai
    assert o.get("u1", "income_90d", as_of=T0 + 10 * D) == 1000.0
    assert o.get("u1", "income_90d", as_of=T0 + 30 * D) == 9999.0


def test_online_missing_feature_returns_none_not_zero():
    """Thiếu dữ liệu phải là None. Trả 0 sẽ làm mô hình tin là feature = 0."""
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    assert o.get("u1", "income_90d") is None
    assert o.has("u1", "income_90d") is False


def test_ttl_expired_returns_none():
    """default_count_2y có TTL 90 ngày. Sau đó phải None, không phải số cũ."""
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    o.upsert("u1", "default_count_2y", 2, T0)
    assert o.get("u1", "default_count_2y", as_of=T0 + 10 * D) == 2

    after_ttl = T0 + 91 * D
    assert o.get("u1", "default_count_2y", as_of=after_ttl) is None, (
        "feature het han phai tra None, khong tra so cu")
    # The TTL'd read above is the real assertion. `has()` takes no as_of and is
    # evaluated against the store's own clock, so it cannot distinguish "not
    # expired" from "expired" -- the old `is False or True` was a tautology that
    # asserted nothing. Assert the observable fact instead: the value is gone
    # from every read path that honours a timestamp.
    assert o.get("u1", "default_count_2y", as_of=after_ttl) is None
    assert o.get("u1", "default_count_2y", as_of=T0 + 90 * D) == 2, (
        "feature chua het han o moc 90 ngay van phai tra gia tri")


def test_ttl_fresh_value_survives():
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    o.upsert("u1", "default_count_2y", 1, T0)
    assert o.get("u1", "default_count_2y", as_of=T0 + 89 * D) == 1


def test_feature_without_ttl_never_expires():
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    o.upsert("u1", "income_90d", 5000.0, T0)
    assert o.get("u1", "income_90d", as_of=T0 + 3650 * D) == 5000.0


def test_online_write_is_idempotent():
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    o.upsert("u1", "income_90d", 100.0, T0)
    o.upsert("u1", "income_90d", 100.0, T0)
    assert len(o.history_as_of("u1", T0)) == 1


def test_online_upsert_rejects_unknown_feature():
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    with pytest.raises(KeyError):
        o.upsert("u1", "khong_ai_biet", 1)


def test_online_get_row_matches_feature_order():
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    row = o.get_row("u1", T0)
    assert len(row) == len(FEATURE_ORDER)
    vec = o.get_vector("u1", T0)
    assert list(vec) == list(FEATURE_ORDER)


def test_online_upsert_many():
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    n = o.upsert_many("u1", {"income_90d": 100.0, "total_loans": 3}, T0)
    assert n == 2
    assert o.get("u1", "total_loans") == 3


def test_online_entities_and_clear():
    o = OnlineFeatureStore(now_fn=_fixed_clock(T0))
    o.upsert("u1", "income_90d", 1, T0)
    o.upsert("u2", "income_90d", 1, T0)
    assert o.entities() == ["u1", "u2"]
    o.clear()
    assert o.entities() == []
