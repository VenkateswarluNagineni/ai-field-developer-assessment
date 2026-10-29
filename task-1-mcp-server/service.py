"""Deterministic demo records and simulated refunds; no external side effects."""

from uuid import uuid4

from schemas import CustomerInput, RefundInput


CUSTOMERS = {
    "CUST-12345": {"customer_id": "CUST-12345", "name": "Alex Morgan", "status": "active"},
    "CUST-00001": {"customer_id": "CUST-00001", "name": "Sam Rivera", "status": "active"},
}


def execute(name: str, arguments: CustomerInput) -> dict:
    customer = CUSTOMERS.get(arguments.customer_id)
    if customer is None:
        raise LookupError("Customer not found")
    if name == "get_customer_record":
        return dict(customer)
    assert isinstance(arguments, RefundInput)
    return {
        "refund_id": f"REF-{uuid4().hex}",
        "customer_id": arguments.customer_id,
        "amount": arguments.amount,
        "reason": arguments.reason,
        "status": "simulated",
    }
