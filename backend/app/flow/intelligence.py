from __future__ import annotations

from typing import Any


class FlowIntelligence:
    """Explainable option-chain structure analysis."""

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            return None if value in (None, "") else float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _get(row: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in row and row[key] is not None:
                return row[key]
        return None

    def normalize(self, chain: dict[str, Any]) -> list[dict[str, Any]]:
        raw: Any = chain.get("option_chain", chain.get("data", chain))
        if isinstance(raw, dict):
            raw = raw.get("options", raw.get("optionChain", raw.get("records", [])))
        if not isinstance(raw, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            # Support strike-level {call:{...}, put:{...}} and flat rows.
            strike = self._get(item, "strike", "strike_price")
            legs = [("CE", item.get("call", item.get("CE", item.get("ce")))),
                    ("PE", item.get("put", item.get("PE", item.get("pe"))))]
            added = False
            for typ, leg in legs:
                if isinstance(leg, dict):
                    row = dict(leg)
                    row["strike"] = strike
                    row["option_type"] = typ
                    rows.append(row)
                    added = True
            if not added:
                row = dict(item)
                typ = str(self._get(row, "option_type", "instrument_type", "type") or "").upper()
                row["option_type"] = "PE" if typ in {"PE", "PUT"} else "CE" if typ in {"CE", "CALL"} else typ
                rows.append(row)
        return rows

    def analyze(self, chain: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = self.normalize(chain)
        old = {(str(r.get("strike")), str(r.get("option_type"))): r for r in (previous or {}).get("rows", [])}
        call_oi = put_oi = call_vol = put_vol = 0.0
        pressure = 0.0
        regimes: list[dict[str, Any]] = []

        for row in rows:
            typ = row.get("option_type")
            if typ not in {"CE", "PE"}:
                continue
            oi = self._num(self._get(row, "oi", "open_interest")) or 0.0
            vol = self._num(self._get(row, "volume", "total_volume")) or 0.0
            price = self._num(self._get(row, "ltp", "last_price", "price"))
            if typ == "CE":
                call_oi += oi; call_vol += vol
            else:
                put_oi += oi; put_vol += vol
            prior = old.get((str(row.get("strike")), str(typ)))
            if not prior:
                continue
            old_oi = self._num(self._get(prior, "oi", "open_interest"))
            old_price = self._num(self._get(prior, "ltp", "last_price", "price"))
            if old_oi in (None, 0) or price is None or old_price in (None, 0):
                continue
            oi_pct = (oi - old_oi) / abs(old_oi) * 100
            price_pct = (price - old_price) / abs(old_price) * 100
            if abs(oi_pct) < 1.0:
                continue
            if typ == "CE":
                event = "LONG_BUILDUP" if price_pct > 0 else "SHORT_BUILDUP" if price_pct < 0 else "OI_BUILDUP"
                pressure += (1 if price_pct > 0 else -0.6 if price_pct < 0 else 0.2) * min(abs(oi_pct), 10) / 10
            else:
                event = "PUT_BUYING" if price_pct > 0 else "PUT_WRITING" if price_pct < 0 else "OI_BUILDUP"
                pressure -= (1 if price_pct > 0 else -0.6 if price_pct < 0 else 0.2) * min(abs(oi_pct), 10) / 10
            regimes.append({"strike": row.get("strike"), "option_type": typ, "event": event,
                             "oi_change_pct": round(oi_pct, 3), "price_change_pct": round(price_pct, 3)})

        pcr_oi = put_oi / call_oi if call_oi else None
        pcr_volume = put_vol / call_vol if call_vol else None
        score = max(-100.0, min(100.0, pressure * 100))
        evidence: list[str] = []
        if pcr_oi is not None:
            if pcr_oi > 1.2: evidence.append("put_oi_dominance")
            elif pcr_oi < 0.8: evidence.append("call_oi_dominance")
        if pcr_volume is not None:
            if pcr_volume > 1.3: evidence.append("put_volume_expansion")
            elif pcr_volume < 0.75: evidence.append("call_volume_expansion")
        if regimes: evidence.append("oi_price_regime_changes")
        bias = "BULLISH" if score >= 15 else "BEARISH" if score <= -15 else "NEUTRAL"
        confidence = "HIGH" if abs(score) >= 60 else "MEDIUM" if abs(score) >= 30 else "LOW"
        return {"bias": bias, "score": round(score, 2), "confidence": confidence,
                "pcr_oi": None if pcr_oi is None else round(pcr_oi, 4),
                "pcr_volume": None if pcr_volume is None else round(pcr_volume, 4),
                "call_oi": call_oi, "put_oi": put_oi, "call_volume": call_vol,
                "put_volume": put_vol, "evidence": evidence, "regimes": regimes,
                "rows": rows,
                "warning": "OI and volume do not uniquely identify opening versus closing trades."}


flow_intelligence = FlowIntelligence()
