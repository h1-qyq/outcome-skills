"""Fail-closed configuration and construction for the server-owned payment router."""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from gateway.orders import OrderStore
from gateway.payments.base import PaymentAdapter
from gateway.payments.clawtip import (
    ClawTipActivation,
    ClawTipPaymentAdapter,
    ClawTipSettings,
)
from gateway.payments.demo import DemoPaymentAdapter
from gateway.payments.router import CurrencyPaymentRouter
from gateway.payments.x402 import (
    AuthenticatedCdpProvider,
    PaymentRequiredBuilder,
    X402Facilitator,
    X402PaymentAdapter,
    X402Settings,
)


Environment = Literal["development", "test", "production"]


class GatewayConfig(BaseModel):
    """Environment values are configuration only; they are never CDP auth proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Environment = "development"
    payment_adapter: Literal["demo", "live"] = "demo"
    x402_pay_to: str = ""
    x402_resource_base_url: str = ""
    clawtip_pay_to: str = ""
    clawtip_slug: str = ""
    clawtip_sm4_key: SecretStr = SecretStr("")
    clawtip_amount_representation: Literal["string", "number"] | None = None
    clawtip_order_records_dir: str = ""
    clawtip_resource_url: str = Field(default="", max_length=128)
    result_access_token_key: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def reject_unready_production(self) -> "GatewayConfig":
        if self.environment == "production" and self.payment_adapter != "live":
            raise ValueError("production payment adapter must be live")
        if self.clawtip_resource_url:
            parsed = urlsplit(self.clawtip_resource_url)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError("ClawTip resource URL must be an absolute HTTPS URL")
        return self


def load_config(environment: Mapping[str, str]) -> GatewayConfig:
    """Load placeholders only; secret values are never logged or returned."""

    return GatewayConfig(
        environment=environment.get("GATEWAY_ENVIRONMENT", "development"),
        payment_adapter=environment.get("PAYMENT_ADAPTER", "demo"),
        x402_pay_to=environment.get("X402_PAY_TO", ""),
        x402_resource_base_url=environment.get("X402_RESOURCE_BASE_URL", ""),
        clawtip_pay_to=environment.get("CLAWTIP_PAY_TO", ""),
        clawtip_slug=environment.get("CLAWTIP_SLUG", ""),
        clawtip_sm4_key=environment.get("CLAWTIP_SM4_KEY", ""),
        clawtip_amount_representation=environment.get("CLAWTIP_AMOUNT_REPRESENTATION") or None,
        clawtip_order_records_dir=environment.get("CLAWTIP_ORDER_RECORDS_DIR", ""),
        clawtip_resource_url=environment.get("CLAWTIP_RESOURCE_URL", ""),
        result_access_token_key=environment.get("RESULT_ACCESS_TOKEN_KEY", ""),
    )


def build_payment_adapter(
    store: OrderStore,
    config: GatewayConfig,
    *,
    cdp_auth_provider: AuthenticatedCdpProvider | None = None,
    clawtip_activation: ClawTipActivation | None = None,
    x402_facilitator: X402Facilitator | None = None,
    x402_payment_required_builder: PaymentRequiredBuilder | None = None,
) -> PaymentAdapter:
    """Build a single router; any missing live deployment gate is an error."""

    if config.payment_adapter == "demo":
        return DemoPaymentAdapter(store, environment=config.environment)
    if config.environment == "production" and (
        x402_facilitator is not None or x402_payment_required_builder is not None
    ):
        raise ValueError("production forbids injected x402 test seams")
    if config.environment == "production" and (
        cdp_auth_provider is None or not cdp_auth_provider.integration_tested
    ):
        raise ValueError("production requires an explicitly authenticated CDP provider")
    if not config.x402_pay_to or not config.x402_resource_base_url:
        raise ValueError("live x402 receiver and resource URL are required")
    clawtip_sm4_key = config.clawtip_sm4_key.get_secret_value()
    if not all((
        config.clawtip_pay_to,
        config.clawtip_slug,
        clawtip_sm4_key,
        config.clawtip_amount_representation,
        config.clawtip_resource_url,
    )):
        raise ValueError("live ClawTip pay_to, codec, amount representation, and resource URL are required")
    if config.environment == "production":
        x402_settings = X402Settings.production(
            pay_to=config.x402_pay_to,
            auth_provider=cdp_auth_provider,
            resource_base_url=config.x402_resource_base_url,
        )
        payment_skill = "clawtip"
    else:
        x402_settings = X402Settings.testnet(
            pay_to=config.x402_pay_to,
            resource_base_url=config.x402_resource_base_url,
        )
        payment_skill = "clawtip-sandbox"
    clawtip_settings = ClawTipSettings(
        pay_to=config.clawtip_pay_to,
        slug=config.clawtip_slug,
        sm4_key=clawtip_sm4_key,
        amount_representation=config.clawtip_amount_representation,
        order_records_dir=Path(config.clawtip_order_records_dir),
        payment_skill=payment_skill,
        environment=config.environment,
        activation=clawtip_activation,
        resource_url=config.clawtip_resource_url,
    )
    return CurrencyPaymentRouter(
        usd=X402PaymentAdapter(
            store,
            x402_settings,
            facilitator=x402_facilitator,
            payment_required_builder=x402_payment_required_builder,
        ),
        cny=ClawTipPaymentAdapter(store, clawtip_settings),
    )
