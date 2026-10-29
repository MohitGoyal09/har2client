"""Difficulty levels L0-L3 and the adaptive promotion / demotion rule (DESIGN-RL.md, "Task generation and curriculum")."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

from har2client.tierb import config as cfg

WINDOW = 64  # episodes in the rolling mean
PROMOTE_AT = 0.7
DEMOTE_AT = 0.2


@dataclass(frozen=True)
class Level:
    index: int
    min_switches: int
    max_switches: int
    noise: float  # share of unrelated traffic in the observation


LEVELS = (
    Level(0, 0, 0, 0.0),
    Level(1, 1, 1, 0.15),
    Level(2, 2, 2, 0.3),
    Level(3, 3, len(cfg.FACTORS), 0.7),
)
MAX_LEVEL = len(LEVELS) - 1


def count_switches(config: dict[str, str]) -> int:
    """Number of switches that differ from the all-off baseline."""
    base = cfg.baseline_config()
    return sum(config[name] != base[name] for name in base)


def level_of(config: dict[str, str]) -> int:
    n = count_switches(config)
    return next(level.index for level in LEVELS if level.min_switches <= n <= level.max_switches)


def _parse_fixed(mode: str | int) -> int | None:
    """`3`, `"L3"` or `"fixed:3"` -> 3; anything else -> None."""
    if isinstance(mode, int):
        return mode
    text = str(mode).removeprefix("fixed:").removeprefix("L")
    return int(text) if text.isdigit() else None


class Curriculum:
    """Picks the level of the next episode.

    `adaptive`: start at `start`; after a full window of episodes at one level, move up when the mean reward is
    >= PROMOTE_AT and down when it is <= DEMOTE_AT, then start a new window. `fixed:<k>` (or `L<k>`) always gives
    level k; `uniform` draws a level at random.
    """

    def __init__(self, mode: str | int = "adaptive", *, start: int = 0, window: int = WINDOW, levels: tuple[int, ...] | None = None):
        self.levels = tuple(levels) if levels else tuple(range(MAX_LEVEL + 1))
        self.window = window
        self._rewards: deque[float] = deque(maxlen=window)
        self.fixed: int | None = None
        self.adaptive = False
        fixed = _parse_fixed(mode)
        if fixed is not None:
            self.fixed = fixed
            if fixed not in self.levels:
                raise ValueError(f"level {fixed} is not available here: {self.levels}")
        elif mode == "adaptive":
            self.adaptive = True
        elif mode != "uniform":
            raise ValueError(f"curriculum must be 'adaptive', 'uniform', 'L<k>' or 'fixed:<k>', got {mode!r}")
        self.level = self.fixed if self.fixed is not None else self._clamp(start)

    def _clamp(self, level: int) -> int:
        return min(max(level, self.levels[0]), self.levels[-1]) if level not in self.levels else level

    def next_level(self, rng: random.Random) -> int:
        return rng.choice(self.levels) if not self.adaptive and self.fixed is None else self.level

    def record(self, reward: float) -> int:
        """Add the reward of an episode played at the current level; returns the (possibly new) level."""
        if not self.adaptive:
            return self.level
        self._rewards.append(reward)
        if len(self._rewards) < self.window:
            return self.level
        mean = sum(self._rewards) / len(self._rewards)
        position = self.levels.index(self.level)
        if mean >= PROMOTE_AT and position + 1 < len(self.levels):
            self.level = self.levels[position + 1]
            self._rewards.clear()
        elif mean <= DEMOTE_AT and position > 0:
            self.level = self.levels[position - 1]
            self._rewards.clear()
        return self.level
