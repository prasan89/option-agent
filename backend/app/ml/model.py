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
    """Chronologically validated supervised baseline; never connected to trading."""

    MIN_SAMPLES = 30
    VALIDATION_FRACTION = 0.20

    def __init__(self) -> None:
        self._model = None
        self._ready = False
        self._training_stats: dict[str, Any] = {}

    @staticmethod
    def _matrix(rows: list[dict[str, Any]]) -> np.ndarray:
        return np.asarray([[float(row.get(f) or 0.0) for f in FEATURES] for row in rows], dtype=float)

    def fit(self, rows: list[dict[str, Any]], labels: list[int]) -> dict[str, Any]:
        if HistGradientBoostingClassifier is None:
            return {"trained": False, "reason": "scikit-learn is not installed"}
        if len(rows) != len(labels) or len(rows) < self.MIN_SAMPLES or len(set(labels)) < 2:
            return {"trained": False, "reason": f"need at least {self.MIN_SAMPLES} rows and both classes"}

        split = max(1, int(len(rows) * (1.0 - self.VALIDATION_FRACTION)))
        if split >= len(rows):
            split = len(rows) - 1
        train_rows, test_rows = rows[:split], rows[split:]
        train_labels, test_labels = labels[:split], labels[split:]
        if len(set(train_labels)) < 2:
            return {"trained": False, "reason": "chronological training portion contains only one class"}

        model = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.05, random_state=42)
        model.fit(self._matrix(train_rows), np.asarray(train_labels, dtype=int))
        self._model = model
        self._ready = True

        validation_accuracy = None
        if test_rows and len(set(test_labels)) >= 1:
            predictions = model.predict(self._matrix(test_rows))
            validation_accuracy = float(np.mean(predictions == np.asarray(test_labels, dtype=int)))
        self._training_stats = {
            "samples": len(rows),
            "train_samples": len(train_rows),
            "validation_samples": len(test_rows),
            "validation_accuracy": None if validation_accuracy is None else round(validation_accuracy, 4),
            "features": list(FEATURES),
            "validation": "chronological_holdout",
        }
        return {"trained": True, **self._training_stats}

    def predict(self, row: dict[str, Any]) -> Prediction:
        if not self._ready or self._model is None:
            return Prediction(0.5, 0.5, "UNKNOWN", False)
        probabilities = self._model.predict_proba(self._matrix([row]))[0]
        classes = list(self._model.classes_)
        up = float(probabilities[classes.index(1)]) if 1 in classes else 0.0
        down = float(probabilities[classes.index(0)]) if 0 in classes else 0.0
        return Prediction(round(up, 4), round(down, 4), "UP" if up >= down else "DOWN", True)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def training_stats(self) -> dict[str, Any]:
        return dict(self._training_stats)


baseline_predictor = BaselinePredictor()
