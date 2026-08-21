from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Sequence


_FREQUENCY_RE = re.compile(
    r"freq\s*\(\s*\d+(?:\s*-\s*\d+)?\s*\).*?=\s*([-+0-9.EeDd]+)\s*\[cm-1\]",
    re.I,
)
_ACTIVITY_RE = re.compile(
    r"(?:raman\s+activity|activity)\s*(?:\([^)]*\))?\s*[=:]\s*([-+0-9.EeDd]+)",
    re.I,
)
_RAMAN_ACTIVE_RE = re.compile(
    r"freq\s*\([^)]*\)\s*=\s*([-+0-9.EeDd]+)\s*\[cm-1\].*?-->.*?\bR\b",
    re.I,
)


def raman_mode_data(run_dir: str | Path):
    """Return Gamma Raman-mode frequencies and available calculated activities."""
    root = Path(run_dir)
    outputs = sorted(
        (
            path for path in root.rglob("*.out")
            if path.is_file() and ("raman" in path.name.lower() or "gamma" in path.name.lower())
        ),
        key=lambda path: (
            "raman-analysis" not in path.name.lower(),
            "attempts" not in {part.lower() for part in path.parts},
            -path.stat().st_mtime_ns,
        ),
    )
    for path in outputs:
        text = path.read_text(encoding="utf-8", errors="replace")
        active_frequencies = [
            float(value.replace("D", "E").replace("d", "e"))
            for value in _RAMAN_ACTIVE_RE.findall(text)
        ]
        frequencies = active_frequencies or [
            float(value.replace("D", "E").replace("d", "e"))
            for value in _FREQUENCY_RE.findall(text)
        ]
        if not frequencies:
            continue
        activities = [
            float(value.replace("D", "E").replace("d", "e"))
            for value in _ACTIVITY_RE.findall(text)
        ]
        modes = []
        for index, frequency in enumerate(frequencies):
            intensity = activities[index] if len(activities) == len(frequencies) else 1.0
            if not any(abs(frequency - previous[0]) < 0.2 for previous in modes):
                modes.append((frequency, max(0.0, intensity)))
        return path, modes, len(activities) == len(frequencies), bool(active_frequencies)
    return None


def broaden_raman_modes(
    modes: Sequence[tuple[float, float]],
    *,
    linewidth_cm1: float = 8.0,
    points: int = 2000,
) -> tuple[list[float], list[float]]:
    """Create a normalized Gaussian Raman spectrum from discrete modes."""
    positive = [(frequency, intensity) for frequency, intensity in modes if frequency >= 0]
    if not positive:
        return [], []
    sigma = max(float(linewidth_cm1), 1.0e-6) / (2.0 * math.sqrt(2.0 * math.log(2.0)))
    lower = max(0.0, min(frequency for frequency, _ in positive) - 5.0 * linewidth_cm1)
    upper = max(frequency for frequency, _ in positive) + 5.0 * linewidth_cm1
    count = max(200, int(points))
    x = [lower + (upper - lower) * index / (count - 1) for index in range(count)]
    y = [
        sum(intensity * math.exp(-0.5 * ((value - frequency) / sigma) ** 2)
            for frequency, intensity in positive)
        for value in x
    ]
    maximum = max(y) if y else 0.0
    if maximum > 0:
        y = [value / maximum for value in y]
    return x, y
