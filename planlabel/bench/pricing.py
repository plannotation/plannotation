# SPDX-License-Identifier: Apache-2.0
"""What a run cost, from the token counts the API reports.

Prices are list prices in US dollars per million tokens, as published when this
table was written; they are an estimate for the report, never a bill. A model not
in the table is reported with no cost rather than a guessed one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from planlabel.bench.runner import Usage

#: When the table below was last checked against the published prices.
PRICES_AS_OF: Final = "2026-06"

_PER_MILLION: Final = 1_000_000


@dataclass(frozen=True)
class Price:
    """Per-million-token prices for one model.

    Attributes:
        input: Uncached input tokens.
        output: Output tokens, thinking included.
        cache_write: Input tokens written to the prompt cache (five-minute lifetime).
        cache_read: Input tokens served from the prompt cache.
    """

    input: float
    output: float
    cache_write: float
    cache_read: float


def _standard(input_price: float, output_price: float) -> Price:
    """Price a model whose cache prices follow the usual multipliers.

    Args:
        input_price: Its input price.
        output_price: Its output price.

    Returns:
        The price, with cache writes at 1.25x and cache reads at 0.1x the input price.
    """
    return Price(input_price, output_price, input_price * 1.25, input_price * 0.1)


PRICES: Final[dict[str, Price]] = {
    "claude-fable-5-1": Price(10.0, 50.0, 12.5, 0.25),
    "claude-fable-5": _standard(10.0, 50.0),
    "claude-opus-5-5": Price(4.0, 20.0, 5.0, 0.20),
    "claude-opus-5": _standard(5.0, 25.0),
    "claude-opus-4-8": _standard(5.0, 25.0),
    "claude-sonnet-5": _standard(2.0, 10.0),
    "claude-sonnet-4-6": _standard(3.0, 15.0),
    "claude-haiku-4-5": _standard(1.0, 5.0),
}


def cost(model: str, usage: Usage) -> float | None:
    """Estimate what one request cost.

    Args:
        model: The model it was sent to.
        usage: The token counts the API reported for it.

    Returns:
        US dollars, or None when the model's price is not in the table.
    """
    price = PRICES.get(model)
    if price is None:
        return None
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    )
    return total / _PER_MILLION
