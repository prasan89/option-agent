from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    from sklearn.ensemble import HistGradientBoostingClassifier
except ImportError:  # pragma: no cover
    HistGradientBoostingClassifier = None


FEATURES = (
    "score",
    "price_change_pct",
    "depth_imbalance",
    "oi_change_pct",
    "volume_change_pct",
    "intelligence_score",
)


@dataclass
class Prediction:
    probability_up: float
    probability_down: float
    predicted_direction: str
    model_ready: bool


class BaselinePredictor:
    """Small supervised baseline. It is deliberately not connected to trading."""

    def __init__(self) -> None:
        self._model = HistGradientBoostingClassifier(max_iter=100, random_state=42) if HistGradientBoostingClassifier else None
        self._ready = False

    @staticmethod
    def _matrix(rows: list[dict[str, Any]]) -> np.ndarray:
        return np.asarray([[float(row.get(f) or 0.0) for f in FEATURES] for row in rows], dtype=float)

    def fit(self, rows: list[dict[str, Any]], labels: list[int]) -> dict[str, Any]:
        if self._model is None:
            return {"trained": False, "reason": "scikit-learn is not installed"}
        if len(rows) < 30 or len(set(labels)) < 2:
            return {"trained": False, "reason": "need at least 30 rows and both classes"}
        self._model.fit(self._matrix(rows), np.asarray(labels, dtype=int))
        self._ready = True
        return {"trained": True, "samples": len(rows), "features": list(FEATURES)}

    def predict(self, row: dict[str, Any]) -> Prediction:
        if not self._ready:
            return Prediction(0.5, 0.5, "UNKNOWN", False)
        probabilities = self._model.predict_proba(self._matrix([row]))[0]
        classes = list(self._model.classes_)
        up = float(probabilities[classes.index(1)]) if 1 in classes else 0.0
        down = float(probabilities[classes.index(0)]) if 0 in classes else 0.0
        return Prediction(round(up, 4), round(down, 4), "UP" if up >= down else "DOWN", True)


baseline_predictor = BaselinePredictor()
