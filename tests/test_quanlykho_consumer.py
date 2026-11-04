import asyncio

import pytest

from pipeline.integration.quanlykho_consumer import (
    MakerCheckerRequired,
    handle_inventory_event,
)


def test_inventory_event_creates_maker_checker_request_in_production():
    created = []

    async def create_request(payload):
        created.append(payload)
        return "approval-1"

    async def run():
        return await handle_inventory_event(
            {
                "event_id": "event-1",
                "event_type": "inventory.request.approved",
                "aggregate_id": "request-1",
                "payload": {
                    "collateral_id": "asset-1",
                    "loan_application_id": "loan-1",
                },
            },
            create_maker_checker_request=create_request,
            environment="production",
        )

    result = asyncio.run(run())

    assert result == {"status": "pending_maker_checker", "request_id": "approval-1"}
    assert created[0]["loan_application_id"] == "loan-1"
    assert created[0]["source_event_id"] == "event-1"


def test_simulation_mode_can_preview_without_moving_money():
    result = asyncio.run(
        handle_inventory_event(
            {
                "event_id": "event-2",
                "event_type": "inventory.request.approved",
                "aggregate_id": "request-2",
                "payload": {"loan_application_id": "loan-2"},
            },
            create_maker_checker_request=None,
            environment="test",
        )
    )

    assert result == {
        "status": "simulation_preview",
        "loan_application_id": "loan-2",
    }


def test_production_never_auto_disburses():
    with pytest.raises(MakerCheckerRequired):
        asyncio.run(
            handle_inventory_event(
                {
                    "event_id": "event-3",
                    "event_type": "inventory.request.approved",
                    "aggregate_id": "request-3",
                    "payload": {"loan_application_id": "loan-3"},
                },
                create_maker_checker_request=None,
                environment="production",
            )
        )
