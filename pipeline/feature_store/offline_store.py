"""
Offline feature store — lịch sử feature theo thời gian, phục vụ huấn luyện.

Đây là nơi **bảo vệ chống rò rỉ tương lai (future leakage)**. Bất biến cốt lõi:

    Nếu thời điểm dự đoán là T, thì mọi feature có event_time > T
    TUYET DOI KHONG duoc dua vao.

Việc lọc nằm ở một chỗ duy nhất (`_visible_history`) mà mọi đường đọc đều
đi qua. Lịch sử truyền vào `fn` cũng đã lọc — nghĩa là ngay cả khi một
feature definition viết sai, nó vẫn không thấy được tương lai.
"""

from __future__ import annotations

from typing import Any, Iterable

from pipeline.feature_store.definitions import (
    FEATURE_ORDER,
    RAW_EVENTS,
    STANDARD_FEATURES,
    WRITABLE_NAMES,
    FeatureDefinition,
    FeatureValue,
    to_utc,
)


class LeakageError(AssertionError):
    """Co du lieu tuong lai lot vao tap tinh feature — dung, khong che."""


class OfflineFeatureStore:
    """
    Luu feature theo (entity_id, name, event_time) va truy van theo thoi diem.

    Dung dict long dict thay vi database: feature store tầng nay can truy van
    dung theo thoi diem va phai chay duoc khong can ha tang. Khi khoi luong lon
    thi thay lop nay, giu nguyen hop dong.
    """

    def __init__(self, definitions: Iterable[FeatureDefinition] = None) -> None:
        self.definitions = {d.name: d for d in (definitions or STANDARD_FEATURES)}
        self.order = tuple(self.definitions)
        # entity_id -> name -> list[FeatureValue] (sap xep theo event_time)
        self._data: dict[str, dict[str, list]] = {}

    # -- ghi --------------------------------------------------------------
    def write(self, values) -> int:
        """
        Ghi mot hoac nhieu feature value. Tra ve so dong da ghi.

        Ghi lai cung (entity, name, event_time) se ghi de chu khong nhan ban —
        chay lai cung du lieu cho cung ket qua (idempotent).
        """
        vals = [values] if isinstance(values, FeatureValue) else list(values)
        for v in vals:
            if v.name not in WRITABLE_NAMES:
                raise KeyError(
                    f"'{v.name}' khong phai feature hay su kien nguon da biet. "
                    f"Co: {sorted(WRITABLE_NAMES)}")
            bucket = self._data.setdefault(v.entity_id, {}).setdefault(v.name, [])
            bucket[:] = [b for b in bucket if b.event_time != v.event_time]
            bucket.append(v)
            bucket.sort(key=lambda b: b.event_time)
        return len(vals)

    # -- tinh toan --------------------------------------------------------
    def materialize(self, entity_id: str, as_of: Any,
                    only: Iterable[str] = None) -> dict:
        """Tinh moi feature cho entity tai thoi diem as_of."""
        as_of = to_utc(as_of)
        names = list(only) if only is not None else list(self.order)
        history = self._visible_history(entity_id, as_of)
        out: dict[str, Any] = {}
        for name in names:
            dfn = self.definitions.get(name)
            if dfn is None:
                raise KeyError(f"feature '{name}' chua dinh nghia")
            out[name] = dfn.compute(entity_id, as_of, history).value
        return out

    def materialize_row(self, entity_id: str, as_of: Any) -> dict:
        """Tra dict theo dung FEATURE_ORDER — thu tu cot cho mo hinh."""
        vals = self.materialize(entity_id, as_of)
        return {name: vals.get(name) for name in FEATURE_ORDER}

    def materialize_dataset(self, entity_ids: Iterable[str], as_of: Any) -> list:
        """Tap huan luyen: moi entity mot hang, cung thu tu cot."""
        return [self.materialize_row(e, as_of) for e in entity_ids]

    def backfill(self, entity_id: str, times: Iterable[Any]) -> list:
        """
        Tinh lich su feature tai nhieu thoi diem — cach tao tap huan luyen ma
        khong ro ri: moi hang chi nhin thay du lieu truoc thoi diem do.
        """
        return [self.materialize_row(entity_id, t) for t in times]


    # -- doc --------------------------------------------------------------
    def _visible_history(self, entity_id: str, as_of: Any) -> list:
        """
        Lich su DA LOC theo as_of — nguon su that duy nhat chong leakage.

        Bat bien: moi feature trong ket qua co event_time <= as_of.
        """
        as_of = to_utc(as_of)
        # Duyet TAT CA ten da ghi (feature + su kien nguon), khong chi feature
        # dinh nghia — neu bo qua su kien nguon thi moi feature cua so ra 0.
        names = set(self.definitions) | set(
            self._data.get(entity_id, {}))
        visible = [
            v
            for name in names
            for v in self._data.get(entity_id, {}).get(name, [])
            if v.event_time <= as_of
        ]
        # Kiem tra bat bien thay vi tin rang filter dung.
        future = [v for v in visible if v.event_time > as_of]
        if future:  # pragma: no cover - khong the xay ra neu filter dung
            raise LeakageError(
                f"{len(future)} feature tuong lai lot qua cho {entity_id} tai {as_of}")
        visible.sort(key=lambda v: v.event_time)
        return visible

    def history_as_of(self, entity_id: str, as_of: Any) -> list:
        """Lich su quan sat duoc tai as_of — dung de audit trong test."""
        return self._visible_history(entity_id, as_of)

    def latest_as_of(self, entity_id: str, as_of: Any) -> dict:
        """Gia tri moi nhat cua tung feature tai as_of (khong tinh lai)."""
        as_of = to_utc(as_of)
        out = {}
        for name in self.definitions:
            vals = [v for v in self._data.get(entity_id, {}).get(name, [])
                    if v.event_time <= as_of]
            if vals:
                out[name] = vals[-1].value
        return out

    def entities(self) -> list:
        return sorted(self._data)

    def value_count(self) -> int:
        return sum(len(b) for e in self._data.values() for b in e.values())

    def assert_no_future_data(self, entity_id: str, as_of: Any) -> int:
        """
        Kiểm tra tường minh LỊCH SỬ QUAN SÁT ĐƯỢC tại as_of.

        Khác `write`: store có thể hợp pháp chứa dữ liệu tương lai — đó là bình
        thường, vì ta luôn ghi sự kiện ngay khi nó xảy ra. Cái không được phép
        là lịch sử *trả về cho người tính feature* chứa bản ghi tương lai.

        Vì vậy hàm này kiểm tra đầu ra của `_visible_history`, không kiểm tra
        dữ liệu gốc. Trả về số bản ghi đã kiểm (>=0 nghĩa là sạch).
        """
        visible = self._visible_history(entity_id, as_of)
        leaked = [v for v in visible if v.event_time > to_utc(as_of)]
        if leaked:  # pragma: no cover - _visible_history đã chặn từ trước
            raise LeakageError(
                f"{len(leaked)} ban ghi tuong lai lot qua cho {entity_id} tai {as_of}")
        return len(visible)
