"""Immutable, server-owned product catalog."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


@dataclass(frozen=True)
class Price:
    currency: Literal["USD", "CNY"]
    minor_units: int


@dataclass(frozen=True)
class Product:
    skill_id: str
    result_name: str
    prices: Mapping[str, Price]


def _prices() -> Mapping[str, Price]:
    return MappingProxyType(
        {
            "USD": Price(currency="USD", minor_units=1),
            "CNY": Price(currency="CNY", minor_units=6),
        }
    )


PRODUCTS: Mapping[str, Product] = MappingProxyType(
    {
        "outcome-offer": Product(
            skill_id="outcome-offer",
            result_name="Outcome Offer",
            prices=_prices(),
        ),
        "proof-pack": Product(
            skill_id="proof-pack",
            result_name="Proof Pack",
            prices=_prices(),
        ),
        "reply-to-close": Product(
            skill_id="reply-to-close",
            result_name="Reply to Close",
            prices=_prices(),
        ),
    }
)


def get_product(skill_id: str) -> Product:
    return PRODUCTS[skill_id]
