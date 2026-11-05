"""Outcome tracking & cohort metrics (Wave 3a).

Durable layer on top of the ``loan_outcomes`` table created by
``deploy/sql/002_outcome_tracking.sql``. Reads/writes through
``pipeline.storage.ledger._connect()`` so the same module works against the
demo SQLite ledger and a Postgres (DATABASE_URL) backend without dialect
branching at every call site.

Outcome lifecycle:

* ``PERFORMING`` — loan disbursed and currently repaying (initial state, set
  by the core-banking disbursement flow).
* ``NON_PERFORMING`` — missed payments, not yet charged off.
* ``DEFAULTED`` — realised default (``actual_default`` true, ``default_date``
  set).
* ``CHARGE_OFF`` — written off.

Each ``application_id`` (the ``loan_applications.id`` PK) has at most one
outcome row; ``record_outcome`` upserts so re-reporting is idempotent.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

from pipeline.storage.ledger import _connect, _write_lock

OUTCOME_STATUSES = frozenset({"PERFORMING", "NON_PERFORMING", "DEFAULTED", "CHARGE_OFF"})
OUTCOME_SOURCES = frozenset({"BANK_STATEMENT", "CORE_BANKING", "EXTERNAL"})

# Mutable columns exposed via ``update_outcome`` (cohort_month is fixed at
# disbursement time and never re-derived after the fact).
_OUTCOME_UPDATE_COLUMNS = frozenset(
    {
        "actual_default",
        "default_date",
        "actual_repayment_amount",
        "total_repaid",
        "num_missed_payments",
        "last_payment_date",
        "outcome_status",
        "source",
    }
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _to_iso_date(value: date | str | None) -> str | None:
    """Normalise a date input to an ISO ``YYYY-MM-DD`` string (or None)."""
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parse_date(value: Any) -> date | None:
    """Parse a DB-returned date (str or ``datetime.date``) into ``date``."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class LoanOutcome(BaseModel):
    """A realised (or initial) loan outcome row."""

    id: int | None = None
    application_id: int
    actual_default: bool = False
    default_date: date | None = None
    actual_repayment_amount: float | None = None
    total_repaid: float | None = None
    num_missed_payments: int = 0
    last_payment_date: date | None = None
    outcome_status: str = "PERFORMING"
    cohort_month: str
    source: str
    updated_at: str | None = None


class OutcomeCreate(BaseModel):
    """Payload for creating a new loan outcome."""

    application_id: int
    actual_default: bool = False
    default_date: date | None = None
    actual_repayment_amount: float | None = None
    total_repaid: float | None = None
    num_missed_payments: int = 0
    last_payment_date: date | None = None
    outcome_status: str = "PERFORMING"
    cohort_month: str
    source: str

    @field_validator("outcome_status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in OUTCOME_STATUSES:
            raise ValueError(
                f"invalid outcome_status {value!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_STATUSES))}"
            )
        return value

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        if value not in OUTCOME_SOURCES:
            raise ValueError(
                f"invalid source {value!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_SOURCES))}"
            )
        return value


class OutcomeUpdate(BaseModel):
    """Partial update for an existing outcome. Omitted fields are untouched."""

    actual_default: bool | None = None
    default_date: date | None = None
    actual_repayment_amount: float | None = None
    total_repaid: float | None = None
    num_missed_payments: int | None = None
    last_payment_date: date | None = None
    outcome_status: str | None = None
    source: str | None = None

    @field_validator("outcome_status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in OUTCOME_STATUSES:
            raise ValueError(
                f"invalid outcome_status {value!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_STATUSES))}"
            )
        return value

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str | None) -> str | None:
        if value is not None and value not in OUTCOME_SOURCES:
            raise ValueError(
                f"invalid source {value!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_SOURCES))}"
            )
        return value


class CohortMetrics(BaseModel):
    """Aggregated risk/repayment metrics for one cohort month (YYYY-MM)."""

    cohort_month: str
    total_loans: int
    defaulted_count: int
    performing_count: int
    non_performing_count: int
    charge_off_count: int
    default_rate: float
    repayment_rate: float
    avg_missed_payments: float
    total_repaid: float


class OverallMetrics(BaseModel):
    """Portfolio-wide outcome metrics across all cohorts."""

    total_loans: int
    defaulted_count: int
    performing_count: int
    non_performing_count: int
    charge_off_count: int
    default_rate: float
    npl_rate: float
    repayment_rate: float
    avg_missed_payments: float
    total_repaid: float


# ---------------------------------------------------------------------------
# Row -> model mapping
# ---------------------------------------------------------------------------
def _row_to_outcome(row: Any) -> LoanOutcome:
    data = dict(row)
    return LoanOutcome(
        id=int(data["id"]) if data.get("id") is not None else None,
        application_id=int(data["application_id"]),
        actual_default=bool(data.get("actual_default") or False),
        default_date=_parse_date(data.get("default_date")),
        actual_repayment_amount=(
            float(data["actual_repayment_amount"])
            if data.get("actual_repayment_amount") is not None
            else None
        ),
        total_repaid=(
            float(data["total_repaid"]) if data.get("total_repaid") is not None else None
        ),
        num_missed_payments=int(data.get("num_missed_payments") or 0),
        last_payment_date=_parse_date(data.get("last_payment_date")),
        outcome_status=str(data.get("outcome_status") or "PERFORMING"),
        cohort_month=str(data.get("cohort_month")),
        source=str(data.get("source")),
        updated_at=str(data.get("updated_at")) if data.get("updated_at") is not None else None,
    )


# ---------------------------------------------------------------------------
# OutcomeTracker
# ---------------------------------------------------------------------------
class OutcomeTracker:
    """CRUD + cohort/overall metrics over the ``loan_outcomes`` table."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path

    # -- write helpers ---------------------------------------------------
    def _application_exists(self, conn: Any, application_id: int) -> bool:
        row = conn.execute(
            "SELECT 1 FROM loan_applications WHERE id = ?", (application_id,)
        ).fetchone()
        return row is not None

    def _existing_outcome(self, conn: Any, application_id: int) -> Any | None:
        return conn.execute(
            "SELECT * FROM loan_outcomes WHERE application_id = ?",
            (application_id,),
        ).fetchone()

    def record_outcome(
        self,
        application_id: int,
        actual_default: bool,
        default_date: date | str | None,
        actual_repayment_amount: float | None,
        total_repaid: float | None,
        num_missed_payments: int,
        last_payment_date: date | str | None,
        outcome_status: str,
        cohort_month: str,
        source: str,
    ) -> LoanOutcome:
        """Create or replace the outcome for ``application_id`` (idempotent).

        Raises ``ValueError`` when the application does not exist or when
        ``outcome_status``/``source`` are outside the allowed set.
        """
        if outcome_status not in OUTCOME_STATUSES:
            raise ValueError(
                f"invalid outcome_status {outcome_status!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_STATUSES))}"
            )
        if source not in OUTCOME_SOURCES:
            raise ValueError(
                f"invalid source {source!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_SOURCES))}"
            )
        now = _now_iso()
        default_date_iso = _to_iso_date(default_date)
        last_payment_date_iso = _to_iso_date(last_payment_date)

        with _write_lock, _connect(self.db_path) as conn:
            if not self._application_exists(conn, int(application_id)):
                raise ValueError(
                    f"cannot record outcome: loan application {application_id} not found"
                )
            existing = self._existing_outcome(conn, int(application_id))
            if existing is not None:
                conn.execute(
                    """
                    UPDATE loan_outcomes SET
                        actual_default = ?, default_date = ?,
                        actual_repayment_amount = ?, total_repaid = ?,
                        num_missed_payments = ?, last_payment_date = ?,
                        outcome_status = ?, cohort_month = ?, source = ?,
                        updated_at = ?
                    WHERE application_id = ?
                    """,
                    (
                        bool(actual_default),
                        default_date_iso,
                        float(actual_repayment_amount)
                        if actual_repayment_amount is not None
                        else None,
                        float(total_repaid) if total_repaid is not None else None,
                        int(num_missed_payments),
                        last_payment_date_iso,
                        outcome_status,
                        cohort_month,
                        source,
                        now,
                        int(application_id),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO loan_outcomes
                        (application_id, actual_default, default_date,
                         actual_repayment_amount, total_repaid,
                         num_missed_payments, last_payment_date,
                         outcome_status, cohort_month, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(application_id),
                        bool(actual_default),
                        default_date_iso,
                        float(actual_repayment_amount)
                        if actual_repayment_amount is not None
                        else None,
                        float(total_repaid) if total_repaid is not None else None,
                        int(num_missed_payments),
                        last_payment_date_iso,
                        outcome_status,
                        cohort_month,
                        source,
                        now,
                    ),
                )
            conn.commit()
            row = self._existing_outcome(conn, int(application_id))
        if row is None:
            raise RuntimeError(f"outcome for application {application_id} was not persisted")
        return _row_to_outcome(row)

    def get_outcome(self, application_id: int) -> LoanOutcome | None:
        """Return the outcome record for ``application_id``, or None."""
        with _connect(self.db_path) as conn:
            row = self._existing_outcome(conn, int(application_id))
        return _row_to_outcome(row) if row is not None else None

    def list_outcomes(
        self,
        cohort_month: str | None = None,
        outcome_status: str | None = None,
        source: str | None = None,
    ) -> list[LoanOutcome]:
        """List outcomes, optionally filtered by cohort/status/source."""
        if outcome_status is not None and outcome_status not in OUTCOME_STATUSES:
            raise ValueError(
                f"invalid outcome_status {outcome_status!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_STATUSES))}"
            )
        if source is not None and source not in OUTCOME_SOURCES:
            raise ValueError(
                f"invalid source {source!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_SOURCES))}"
            )
        conditions: list[str] = []
        params: list[Any] = []
        if cohort_month is not None:
            conditions.append("cohort_month = ?")
            params.append(cohort_month)
        if outcome_status is not None:
            conditions.append("outcome_status = ?")
            params.append(outcome_status)
        if source is not None:
            conditions.append("source = ?")
            params.append(source)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM loan_outcomes{where} ORDER BY id ASC"
        with _connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_outcome(r) for r in rows]

    def update_outcome(self, application_id: int, **updates: Any) -> LoanOutcome | None:
        """Partially update an existing outcome.

        Only documented outcome columns may be changed; unknown keys raise
        ``ValueError``. Returns None when no outcome exists for the
        application.
        """
        unknown = set(updates) - _OUTCOME_UPDATE_COLUMNS
        if unknown:
            raise ValueError(
                f"unknown outcome update field(s): {', '.join(sorted(unknown))}"
            )
        if "outcome_status" in updates and updates["outcome_status"] not in OUTCOME_STATUSES:
            raise ValueError(
                f"invalid outcome_status {updates['outcome_status']!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_STATUSES))}"
            )
        if "source" in updates and updates["source"] not in OUTCOME_SOURCES:
            raise ValueError(
                f"invalid source {updates['source']!r}; expected one of "
                f"{', '.join(sorted(OUTCOME_SOURCES))}"
            )
        if not updates:
            return self.get_outcome(int(application_id))

        now = _now_iso()
        set_clauses: list[str] = []
        params: list[Any] = []
        for column in sorted(_OUTCOME_UPDATE_COLUMNS):
            if column not in updates:
                continue
            value = updates[column]
            if column in ("default_date", "last_payment_date"):
                value = _to_iso_date(value)
            elif column in ("actual_repayment_amount", "total_repaid"):
                value = float(value) if value is not None else None
            elif column == "num_missed_payments":
                value = int(value) if value is not None else None
            elif column == "actual_default":
                value = bool(value)
            set_clauses.append(f"{column} = ?")
            params.append(value)
        set_clauses.append("updated_at = ?")
        params.append(now)
        params.append(int(application_id))

        with _write_lock, _connect(self.db_path) as conn:
            existing = self._existing_outcome(conn, int(application_id))
            if existing is None:
                return None
            conn.execute(
                f"UPDATE loan_outcomes SET {', '.join(set_clauses)} WHERE application_id = ?",
                params,
            )
            conn.commit()
            row = self._existing_outcome(conn, int(application_id))
        return _row_to_outcome(row) if row is not None else None

    # -- metrics ----------------------------------------------------------
    def compute_cohort_metrics(
        self, cohort_month: str | None = None
    ) -> list[CohortMetrics]:
        """Cohort metrics (default/repayment/avg missed payments).

        When ``cohort_month`` is given only that cohort is aggregated;
        otherwise one entry per cohort is returned.
        """
        if cohort_month is not None:
            where = "WHERE cohort_month = ?"
            params: tuple[Any, ...] = (cohort_month,)
        else:
            where = ""
            params = ()
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT cohort_month,
                       COUNT(*) AS total_loans,
                       SUM(CASE WHEN actual_default THEN 1 ELSE 0 END) AS defaulted_count,
                       SUM(CASE WHEN outcome_status = 'PERFORMING' THEN 1 ELSE 0 END)
                           AS performing_count,
                       SUM(CASE WHEN outcome_status = 'NON_PERFORMING' THEN 1 ELSE 0 END)
                           AS non_performing_count,
                       SUM(CASE WHEN outcome_status = 'CHARGE_OFF' THEN 1 ELSE 0 END)
                           AS charge_off_count,
                       AVG(num_missed_payments) AS avg_missed_payments,
                       COALESCE(SUM(total_repaid), 0) AS total_repaid
                FROM loan_outcomes
                {where}
                GROUP BY cohort_month
                ORDER BY cohort_month
                """,
                params,
            ).fetchall()
        out: list[CohortMetrics] = []
        for r in rows:
            total = int(r["total_loans"])
            defaulted = int(r["defaulted_count"] or 0)
            performing = int(r["performing_count"] or 0)
            default_rate = round(defaulted / total, 4) if total else 0.0
            repayment_rate = round(performing / total, 4) if total else 0.0
            out.append(
                CohortMetrics(
                    cohort_month=str(r["cohort_month"]),
                    total_loans=total,
                    defaulted_count=defaulted,
                    performing_count=performing,
                    non_performing_count=int(r["non_performing_count"] or 0),
                    charge_off_count=int(r["charge_off_count"] or 0),
                    default_rate=default_rate,
                    repayment_rate=repayment_rate,
                    avg_missed_payments=round(float(r["avg_missed_payments"] or 0.0), 4),
                    total_repaid=round(float(r["total_repaid"] or 0.0), 2),
                )
            )
        return out

    def compute_overall_metrics(self) -> OverallMetrics:
        """Portfolio-wide outcome metrics across all cohorts."""
        with _connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_loans,
                    SUM(CASE WHEN actual_default THEN 1 ELSE 0 END) AS defaulted_count,
                    SUM(CASE WHEN outcome_status = 'PERFORMING' THEN 1 ELSE 0 END)
                        AS performing_count,
                    SUM(CASE WHEN outcome_status = 'NON_PERFORMING' THEN 1 ELSE 0 END)
                        AS non_performing_count,
                    SUM(CASE WHEN outcome_status = 'CHARGE_OFF' THEN 1 ELSE 0 END)
                        AS charge_off_count,
                    AVG(num_missed_payments) AS avg_missed_payments,
                    COALESCE(SUM(total_repaid), 0) AS total_repaid
                FROM loan_outcomes
                """
            ).fetchone()
        total = int(row["total_loans"])
        defaulted = int(row["defaulted_count"] or 0)
        performing = int(row["performing_count"] or 0)
        non_performing = int(row["non_performing_count"] or 0)
        charge_off = int(row["charge_off_count"] or 0)
        default_rate = round(defaulted / total, 4) if total else 0.0
        npl_rate = round(((total - performing) / total), 4) if total else 0.0
        repayment_rate = round(performing / total, 4) if total else 0.0
        return OverallMetrics(
            total_loans=total,
            defaulted_count=defaulted,
            performing_count=performing,
            non_performing_count=non_performing,
            charge_off_count=charge_off,
            default_rate=default_rate,
            npl_rate=npl_rate,
            repayment_rate=repayment_rate,
            avg_missed_payments=round(float(row["avg_missed_payments"] or 0.0), 4),
            total_repaid=round(float(row["total_repaid"] or 0.0), 2),
        )

    def get_outstanding_loans(self) -> list[dict[str, Any]]:
        """Approved/disbursed loans that still have no outcome record.

        These are the loans still "performing" / outstanding from an outcome
        tracking perspective. Returns application-level rows (not outcome
        rows, since none exist yet).
        """
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT la.id, la.application_id, la.thread_id, la.status,
                       la.decision, la.risk_score, la.risk_level,
                       la.created_at, la.updated_at
                FROM loan_applications la
                WHERE la.status = 'APPROVED'
                  AND NOT EXISTS (
                      SELECT 1 FROM loan_outcomes lo
                      WHERE lo.application_id = la.id
                  )
                ORDER BY la.id ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]
