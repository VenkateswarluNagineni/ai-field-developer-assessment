"""Single source of truth for advertised schemas and runtime validation."""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


CustomerId = Annotated[str, StringConstraints(strict=True, pattern=r"^CUST-[0-9]{5}$")]


class CustomerInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    customer_id: CustomerId

    @field_validator("customer_id")
    @classmethod
    def exact_customer_id(cls, value: str) -> str:
        if re.fullmatch(r"CUST-[0-9]{5}", value) is None:
            raise ValueError("Must be CUST- followed by exactly five ASCII digits")
        return value


class RefundInput(CustomerInput):
    amount: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    reason: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=10)]


INPUT_MODELS = {
    "get_customer_record": CustomerInput,
    "trigger_refund": RefundInput,
}
