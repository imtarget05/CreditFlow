"""
Online feature store — phuc vu feature cho request dang chay.

Khac offline store o ba diem, ca ba deu la he qua cua viec phuc vu thoi gian
thuc:

    1. Chi giu trang thai HIEN TAI, khong giu lich su (tiet kiem bo nho)
    2. Ton trong TTL — feature qua han thi tra None, khong tra so cu am tham
    3. Dung CHUNG FeatureDefinition voi offline store, de train/serve parity

Diem 3 la ly do file nay khong tu viet lai cong thuc. Moi phep tinh deu goi
lai `FeatureDefinition.compute` voi mot lich su da loc theo thoi diem hien tai —
ngay ca khi ai do goi online store voi `as_of` qua khu, ket qua van dung nhu
offline. Parity khong phai dieu duoc hy vong.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from pipeline.feature_store.definitions import (
    FEATURE_ORDER,
    STANDARD_FEATURES,
    WRITABLE_NAMES,
    FeatureDefinition,
    FeatureValue,
    to_utc,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OnlineFeatureStore:
    """
    Trang thai feature hien tai theo entity.

    `now_fn` mac dinh la `utcnow`, nhung co the inject de test TTL va
    train/serve parity mot cach xac dinh — khong phu thuoc dong ho that.
    """

    def __init__(self, definitions: Iterable[FeatureDefinition] = None,
                 now_fn=None) -> None:
        self.definitions = {d.name: d for d in (definitions or STANDARD_FEATURES)}
        self.order = tuple(self.definitions)
        self._now_fn = now_fn or utcnow
        # entity_id -> list[FeatureValue] (moi feature, khong loc theo ten)
        self._state: dict[str, list] = {}

    def now(self) -> datetime:
        return to_utc(self._now_fn())

    # -- ghi --------------------------------------------------------------
    def upsert(self, entity_id: str, name: str, value: Any,
               event_time: Any = None) -> FeatureValue:
        """
        Ghi feature cho entity.

        Feature da ton tai cung ten se bi thay the — day la trang thai hien
        tai, khong phai lich su. Ghi lai cung timestamp la idempotent.
        """
        if name not in WRITABLE_NAMES:
            raise KeyError(
                f"'{name}' khong phai feature hay su kien nguon da biet. "
                f"Co: {sorted(WRITABLE_NAMES)}")
        fv = FeatureValue(
            name=name, value=value,
            event_time=to_utc(event_time) if event_time is not None else self.now(),
            entity_id=entity_id)
        bucket = self._state.setdefault(entity_id, [])
        bucket[:] = [b for b in bucket
                     if not (b.name == name and b.event_time == fv.event_time)]
        bucket.append(fv)
        return fv

    def upsert_many(self, entity_id: str, values: dict,
                    event_time: Any = None) -> int:
        ts = to_utc(event_time) if event_time is not None else self.now()
        for name, value in values.items():
            self.upsert(entity_id, name, value, ts)
        return len(values)


    # -- doc --------------------------------------------------------------
    def get(self, entity_id: str, name: str, as_of: Any = None) -> Any:
        """
        Gia tri hien tai cua mot feature.

        Tra None neu khong co, neu da het TTL, hoac neu ban ghi moi nhat
        nam sau as_of (khong duoc nhin thay tuong lai).
        """
        fv = self._lookup(entity_id, name, as_of)
        return None if fv is None else fv.value

    def get_vector(self, entity_id: str, as_of: Any = None) -> dict:
        """Vector theo dung FEATURE_ORDER — thu tu cot khoi hao mo hinh."""
        as_of = to_utc(as_of) if as_of is not None else self.now()
        return {name: self.get(entity_id, name, as_of) for name in FEATURE_ORDER}

    def get_row(self, entity_id: str, as_of: Any = None) -> list:
        """Vector dang list — thu tu khop voi cot cua mo hinh."""
        vec = self.get_vector(entity_id, as_of)
        return [vec[name] for name in FEATURE_ORDER]

    def compute_feature(self, entity_id: str, name: str, as_of: Any = None) -> Any:
        """
        Tinh lai feature bang DUNG cong thuc dung khi train.

        Day la cau noi train/serve parity: no goi chinh
        `FeatureDefinition.compute` voi lich su da loc theo as_of, giong het
        cach OfflineFeatureStore.materialize lam.
        """
        dfn = self.definitions.get(name)
        if dfn is None:
            raise KeyError(f"feature '{name}' chua dinh nghia")
        as_of = to_utc(as_of) if as_of is not None else self.now()
        return dfn.compute(entity_id, as_of, self._visible(entity_id, as_of)).value

    def _lookup(self, entity_id: str, name: str, as_of: Any):
        as_of = to_utc(as_of) if as_of is not None else self.now()
        dfn = self.definitions[name]
        candidates = [v for v in self._state.get(entity_id, [])
                      if v.name == name and v.event_time <= as_of]
        if not candidates:
            return None
        latest = max(candidates, key=lambda v: v.event_time)
        if dfn.ttl_seconds is not None:
            if (as_of - latest.event_time).total_seconds() > dfn.ttl_seconds:
                return None  # het han — tra None, khong tra so cu
        return latest

    def _visible(self, entity_id: str, as_of: Any) -> list:
        """Lich su quan sat duoc: khong ban ghi nao nam sau as_of."""
        as_of = to_utc(as_of)
        return sorted(
            (v for v in self._state.get(entity_id, []) if v.event_time <= as_of),
            key=lambda v: v.event_time)

    def history_as_of(self, entity_id: str, as_of: Any = None) -> list:
        return self._visible(entity_id, as_of or self.now())

    def has(self, entity_id: str, name: str) -> bool:
        return self._lookup(entity_id, name, self.now()) is not None

    def entities(self) -> list:
        return sorted(self._state)

    def clear(self) -> None:
        self._state.clear()
