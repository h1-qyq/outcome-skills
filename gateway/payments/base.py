"""Minimal payment provider contract."""

from abc import ABC, abstractmethod

from gateway.contracts import Order, PaymentRequirement


class PaymentError(Exception):
    """Base class for expected payment-provider outcomes."""


class InvalidPaymentProof(PaymentError):
    """A supplied payment proof is not valid for this order."""


class PaymentRequirementExpired(PaymentError):
    """The quoted payment requirement is no longer payable."""


class PaymentProofExpired(PaymentError):
    """A formerly valid proof expired before settlement."""


class PaymentAlreadySettled(PaymentError):
    """The provider reports that this proof was already settled."""


class PaymentRequirementUnavailable(PaymentError):
    """The provider cannot supply a usable payment requirement."""


class PaymentProcessing(PaymentError):
    """The provider reports a non-terminal payment that must not unlock work."""


class PaymentAdapter(ABC):
    @property
    @abstractmethod
    def production_ready(self) -> bool:
        """Whether this adapter is safe to use for real production settlement."""

    @abstractmethod
    def requirement(self, order: Order) -> PaymentRequirement:
        """Return a server-owned payment requirement for an order."""

    @abstractmethod
    def verify(self, order: Order, proof: str) -> bool:
        """Read-only proof validation. It must not change order state."""

    @abstractmethod
    def settle(self, order: Order, proof: str) -> Order:
        """Validate a proof and perform the one allowed paid transition."""
