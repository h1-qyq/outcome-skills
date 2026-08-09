"""Server-owned currency routing; buyers never choose a payment provider."""

from gateway.contracts import Order, PaymentRequirement
from gateway.payments.base import PaymentAdapter


class CurrencyPaymentRouter(PaymentAdapter):
    def __init__(self, *, usd: PaymentAdapter, cny: PaymentAdapter) -> None:
        self._usd = usd
        self._cny = cny

    @property
    def production_ready(self) -> bool:
        return self._usd.production_ready and self._cny.production_ready

    def requirement(self, order: Order) -> PaymentRequirement:
        return self._adapter(order).requirement(order)

    def verify(self, order: Order, proof: str) -> bool:
        return self._adapter(order).verify(order, proof)

    def settle(self, order: Order, proof: str) -> Order:
        return self._adapter(order).settle(order, proof)

    def payment_response(self, order_id: str) -> str | None:
        response = getattr(self._usd, "payment_response", None)
        return response(order_id) if callable(response) else None

    def resume_settlement(self, order: Order, proof: str) -> Order:
        resume = getattr(self._adapter(order), "resume_settlement", None)
        if not callable(resume):
            raise RuntimeError("payment adapter cannot recover settlement")
        return resume(order, proof)

    def _adapter(self, order: Order) -> PaymentAdapter:
        return self._usd if order.currency == "USD" else self._cny
