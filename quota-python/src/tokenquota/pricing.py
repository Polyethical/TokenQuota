"""Per-model prices used for dollar-denominated budgets.

Prices are supplied by you. Provider prices change, so keep your price file
under version control with the source and date you copied the numbers from.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Union

from .errors import UnknownModelError


@dataclass(frozen=True)
class ModelPrice:
    """Price in US dollars per one million tokens."""

    input_per_mtok: float
    output_per_mtok: float

    def __post_init__(self) -> None:
        if self.input_per_mtok < 0 or self.output_per_mtok < 0:
            raise ValueError("prices must be non-negative")


class Pricing:
    """A table of model prices.

    Costs are returned in integer micro-dollars (1 USD = 1,000,000), which
    avoids floating-point drift when many small costs are added together.
    Conveniently, ``tokens * price_per_million_tokens`` is already micro-dollars.
    """

    def __init__(
        self,
        models: Mapping[str, Union[ModelPrice, Mapping[str, float]]],
        *,
        source: Optional[str] = None,
        updated: Optional[str] = None,
    ) -> None:
        self.source = source
        self.updated = updated
        self._models = {}
        for name, price in models.items():
            if not isinstance(price, ModelPrice):
                price = ModelPrice(
                    input_per_mtok=float(price["input_per_mtok"]),
                    output_per_mtok=float(price["output_per_mtok"]),
                )
            self._models[name] = price

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "Pricing":
        """Load a JSON file shaped like ``prices.example.json``."""
        data = json.loads(Path(path).read_text())
        return cls(data["models"], source=data.get("source"), updated=data.get("updated"))

    def __contains__(self, model: object) -> bool:
        return model in self._models

    def price(self, model: str) -> ModelPrice:
        try:
            return self._models[model]
        except KeyError:
            raise UnknownModelError(
                f"no price configured for model {model!r}; add it to your price table"
            ) from None

    def cost_micro_usd(self, model: str, input_tokens: int, output_tokens: int) -> int:
        p = self.price(model)
        return math.ceil(input_tokens * p.input_per_mtok + output_tokens * p.output_per_mtok)

    def worst_case_micro_usd(self, model: str, tokens: int) -> int:
        """Cost if every token were billed at the model's higher rate."""
        p = self.price(model)
        return math.ceil(tokens * max(p.input_per_mtok, p.output_per_mtok))
