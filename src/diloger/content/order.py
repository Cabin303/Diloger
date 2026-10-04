"""Prompt ordering. Deterministic given a seed."""

from __future__ import annotations

import random
from typing import Sequence

from diloger.domain.models import Order, Prompt


def build_order(prompts: Sequence[Prompt], order: Order, seed: int | None = None) -> list[str]:
    ids = [p.id for p in prompts]
    if order is Order.SEQUENTIAL:
        return ids
    rng = random.Random(seed)
    shuffled = list(ids)
    rng.shuffle(shuffled)
    return shuffled
