"""Reconciliation Engine — internal ledger vs external bank statements.

Compares CreditFlow disbursement records (durable ledger in
``pipeline.storage.ledger``) against external bank statement records to detect
discrepancies (amount mismatches, missing transactions, duplicates) and to
produce reconciliation reports.
"""
from __future__ import annotations

import csv
import json
import threading
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pipeline.storage.ledger import list_disbursements


class DiscrepancyType(str, Enum):
    AMOUNT_MISMATCH = "amount_mismatch"
    MISSING_EXTERNAL = "missing_external"
    MISSING_INTERNAL = "missing_internal"
    DUPLICATE_EXTERNAL = "duplicate_external"


class ReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"
    PENDING = "PENDING"


class BankStatementRecord(BaseModel):
    reference_code: str = Field(..., min_length=1)
    transaction_id: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    amount: float = Field(..., ge=0)
    posted_at: str
    status: str = "POSTED"
    currency: str = "VND"


class ReconciliationEntry(BaseModel):
    transaction_id: str
    reference_code: str
    internal_amount: float | None = None
    external_amount: float | None = None
    status: ReconciliationStatus = ReconciliationStatus.PENDING
    matched: bool = False
    internal_record: dict[str, Any] | None = None
    external_record: dict[str, Any] | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Discrepancy(BaseModel):
    type: DiscrepancyType
    transaction_id: str | None = None
    reference_code: str | None = None
    description: str
    internal: dict[str, Any] | None = None
    external: dict[str, Any] | None = None


class ReconciliationReport(BaseModel):
    date: str
    generated_at: str
    internal_count: int = 0
    external_count: int = 0
    matched_count: int = 0
    mismatch_count: int = 0
    pending_count: int = 0
    discrepancy_count: int = 0
    entries: list[ReconciliationEntry] = Field(default_factory=list)
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    summary: str = ""


_AMOUNT_TOLERANCE = 0.01


class ReconciliationEngine:
    """Reconciles the internal disbursement ledger against external bank statements."""

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        external_statements: list[dict[str, Any]] | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else None
        self._lock = threading.RLock()
        self._records: list[BankStatementRecord] = []
        self._by_reference: dict[str, list[BankStatementRecord]] = {}
        if external_statements:
            for record in external_statements:
                self.add_bank_statement(record)

    def add_bank_statement(self, record: dict[str, Any] | BankStatementRecord) -> None:
        rec = record if isinstance(record, BankStatementRecord) else BankStatementRecord(**record)
        with self._lock:
            self._records.append(rec)
            self._by_reference.setdefault(rec.reference_code, []).append(rec)

    def load_bank_statements(self, path: Path | str) -> int:
        """Load external bank statements from a JSON or CSV file. Returns record count."""
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".csv":
            reader = csv.DictReader(text.splitlines())
            rows: list[dict[str, Any]] = [dict(row) for row in reader]
            for row in rows:
                if row.get("amount") is not None:
                    row["amount"] = float(row["amount"])
        else:
            data = json.loads(text)
            rows = data.get("records", data) if isinstance(data, dict) else data
        count = 0
        for row in rows:
            self.add_bank_statement(row)
            count += 1
        return count

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _internal_records(self, date: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            records = list(list_disbursements(db_path=self.db_path))
        if date:
            records = [r for r in records if str(r.get("disbursed_at", "")).startswith(date)]
        return records

    def _find_external(self, reference_code: str) -> BankStatementRecord | None:
        with self._lock:
            matches = self._by_reference.get(reference_code, [])
        return matches[0] if matches else None

    def _all_external(self) -> list[BankStatementRecord]:
        with self._lock:
            return list(self._records)

    def _build_entry(
        self,
        transaction_id: str,
        reference_code: str,
        internal: dict[str, Any] | None,
        external: BankStatementRecord | None,
    ) -> ReconciliationEntry:
        internal_amount = float(internal.get("loan_amount") or 0) if internal else None
        external_amount = float(external.amount) if external else None
        matched = False
        status = ReconciliationStatus.PENDING
        details: dict[str, Any] = {}
        if internal is not None:
            details["internal_status"] = internal.get("status")
            details["internal_reference"] = internal.get("contract_code")
        if external is not None:
            details["external_status"] = external.status
            details["external_posted_at"] = external.posted_at
        if internal is not None and external is not None and external.status == "POSTED":
            internal_amount = float(internal.get("loan_amount") or 0)
            amount_ok = external_amount is not None and abs(external_amount - internal_amount) <= _AMOUNT_TOLERANCE
            account_ok = True
            if external.account_number:
                account_ok = external.account_number == str(internal.get("account_number", ""))
            matched = amount_ok and account_ok
            status = ReconciliationStatus.MATCHED if matched else ReconciliationStatus.MISMATCH
        return ReconciliationEntry(
            transaction_id=transaction_id,
            reference_code=reference_code,
            internal_amount=internal_amount,
            external_amount=external_amount,
            status=status,
            matched=matched,
            internal_record=internal,
            external_record=external.model_dump() if external else None,
            details=details,
        )

    def reconcile_transaction(self, transaction_id: str) -> ReconciliationEntry:
        """Reconcile a single disbursement against its external bank statement.

        The internal record is located by matching ``transaction_id`` against any
        ledger identifier (id, application_id, contract_code, idempotency_key).
        """
        internal = None
        for record in self._internal_records():
            candidates = {
                str(record.get("id")),
                str(record.get("application_id")),
                str(record.get("contract_code")),
                str(record.get("idempotency_key")),
                str(record.get("reference_code")),
            }
            if transaction_id in candidates:
                internal = record
                break
        if internal is None:
            raise LookupError(f"no internal disbursement record for {transaction_id}")

        reference_code = str(
            internal.get("contract_code")
            or internal.get("application_id")
            or internal.get("idempotency_key")
            or transaction_id
        )
        external = self._find_external(reference_code)
        return self._build_entry(transaction_id, reference_code, internal, external)

    def reconcile_daily(self, date: str | None = None) -> list[ReconciliationEntry]:
        """Reconcile all disbursements, optionally filtered by disbursed_at date."""
        entries: list[ReconciliationEntry] = []
        for record in self._internal_records(date):
            reference_code = str(
                record.get("contract_code")
                or record.get("application_id")
                or record.get("idempotency_key")
                or str(record.get("id"))
            )
            external = self._find_external(reference_code)
            entries.append(self._build_entry(reference_code, reference_code, record, external))
        return entries

    def detect_discrepancies(self, date: str | None = None) -> list[Discrepancy]:
        """Find amount mismatches, missing transactions and duplicate externals."""
        discrepancies: list[Discrepancy] = []
        internal_records = self._internal_records(date)
        external_records = self._all_external()
        matched_external: dict[int, bool] = {id(r): False for r in external_records}

        for record in internal_records:
            reference_code = str(
                record.get("contract_code")
                or record.get("application_id")
                or record.get("idempotency_key")
                or str(record.get("id"))
            )
            matches = [r for r in external_records if r.reference_code == reference_code]
            if not matches:
                discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.MISSING_EXTERNAL,
                        transaction_id=str(record.get("id")),
                        reference_code=reference_code,
                        description=(
                            f"internal disbursement {reference_code} has no external bank statement"
                        ),
                        internal=record,
                        external=None,
                    )
                )
                continue
            external = matches[0]
            for m in matches:
                matched_external[id(m)] = True
            internal_amount = float(record.get("loan_amount") or 0)
            if abs(float(external.amount) - internal_amount) > _AMOUNT_TOLERANCE:
                discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.AMOUNT_MISMATCH,
                        transaction_id=str(record.get("id")),
                        reference_code=reference_code,
                        description=(
                            f"amount mismatch for {reference_code}: "
                            f"internal={internal_amount}, external={external.amount}"
                        ),
                        internal=record,
                        external=external.model_dump(),
                    )
                )

        reference_counts: dict[str, int] = {}
        for record in external_records:
            reference_counts[record.reference_code] = reference_counts.get(record.reference_code, 0) + 1
        for reference_code, count in reference_counts.items():
            if count > 1:
                discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.DUPLICATE_EXTERNAL,
                        reference_code=reference_code,
                        description=(
                            f"external bank statement contains {count} records for reference {reference_code}"
                        ),
                        internal=None,
                        external={
                            "count": count,
                            "records": [
                                r.model_dump()
                                for r in external_records
                                if r.reference_code == reference_code
                            ],
                        },
                    )
                )

        for record in external_records:
            if matched_external[id(record)]:
                continue
            reference_code = record.reference_code
            has_internal = any(
                str(r.get("contract_code") or r.get("application_id") or r.get("idempotency_key"))
                == reference_code
                for r in internal_records
            )
            if not has_internal:
                discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.MISSING_INTERNAL,
                        transaction_id=record.transaction_id,
                        reference_code=reference_code,
                        description=(
                            f"external bank statement record {reference_code} has no internal disbursement"
                        ),
                        internal=None,
                        external=record.model_dump(),
                    )
                )
        return discrepancies

    def generate_reconciliation_report(self, date: str | None = None) -> ReconciliationReport:
        """Produce a reconciliation report for a date (defaults to all records)."""
        report_date = date or self._now()[:10]
        entries = self.reconcile_daily(date)
        discrepancies = self.detect_discrepancies(date)
        matched = sum(1 for e in entries if e.status == ReconciliationStatus.MATCHED)
        mismatched = sum(1 for e in entries if e.status == ReconciliationStatus.MISMATCH)
        pending = sum(1 for e in entries if e.status == ReconciliationStatus.PENDING)
        return ReconciliationReport(
            date=report_date,
            generated_at=self._now(),
            internal_count=len(entries),
            external_count=len(self._all_external()),
            matched_count=matched,
            mismatch_count=mismatched,
            pending_count=pending,
            discrepancy_count=len(discrepancies),
            entries=entries,
            discrepancies=discrepancies,
            summary=(
                f"{matched} matched, {mismatched} mismatched, {pending} pending, "
                f"{len(discrepancies)} discrepancies"
            ),
        )
