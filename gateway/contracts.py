"""Public, amount-free contracts for creating and paying for an order."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Currency = Literal["USD", "CNY"]
OrderStatus = Literal[
    "created",
    "payment-required",
    "processing",
    "paid",
    "generating",
    "delivered",
    "failed",
    "expired",
]


class OrderRequest(BaseModel):
    """Buyer-supplied order intent. Prices are intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    currency: Currency
    locale: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    @field_validator("skill_id", "locale", "idempotency_key")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("input_text")
    @classmethod
    def validate_input_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        if len(value) > 12_000:
            raise ValueError("input_text must be at most 12,000 Unicode code points")
        return value


class Order(BaseModel):
    """Persistent order state safe to return to a caller."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    skill_id: str
    input_hash: str
    currency: Currency
    locale: str
    amount_minor: int = Field(gt=0)
    idempotency_key: str
    status: OrderStatus
    created_at: datetime
    expires_at: datetime


class PaymentRequirement(BaseModel):
    """Server-generated payment requirement for a single order."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    currency: Currency
    amount_minor: int = Field(gt=0)
    expires_at: datetime
    provider_data: "PublicPaymentProviderData | None" = None


class X402PaymentProviderData(BaseModel):
    """Public v2 x402 instructions. No wallet or facilitator credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["x402"] = "x402"
    x402_version: Literal[2] = 2
    scheme: Literal["exact"] = "exact"
    network: Literal["eip155:84532", "eip155:8453"]
    pay_to: str = Field(min_length=1)
    price: Literal["$0.01"] = "$0.01"
    atomic_amount: Literal["10000"] = "10000"
    facilitator_url: str = Field(min_length=1)
    required_header: Literal["PAYMENT-REQUIRED"] = "PAYMENT-REQUIRED"
    signature_header: Literal["PAYMENT-SIGNATURE"] = "PAYMENT-SIGNATURE"
    response_header: Literal["PAYMENT-RESPONSE"] = "PAYMENT-RESPONSE"
    payment_required: str = Field(min_length=1)


class ClawTipPaymentProviderData(BaseModel):
    """Safe fields an Agent needs for the documented local ClawTip handoff."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["clawtip"] = "clawtip"
    order_no: str = Field(min_length=1, max_length=32)
    indicator: str = Field(pattern=r"^[0-9a-f]{32}$")
    amount: Literal[6] = 6
    pay_to: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    encrypted_data: str = Field(min_length=1)
    payment_skill: Literal["clawtip", "clawtip-sandbox"]
    skill_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=128)
    resource_url: str = Field(min_length=1, max_length=128)


PublicPaymentProviderData = Annotated[
    X402PaymentProviderData | ClawTipPaymentProviderData,
    Field(discriminator="provider"),
]
