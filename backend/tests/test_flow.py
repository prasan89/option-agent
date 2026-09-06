from app.flow.detector import FlowDetector
from app.flow.models import MarketSnapshot


def test_aggressive_buy_signal_from_price_and_depth() -> None:
    detector = FlowDetector()
    assert detector.update(MarketSnapshot("1", 1, ltp=100.0, best_bid=99.0, best_ask=100.0, bid_qty=100, ask_qty=100)) is None
    signal = detector.update(MarketSnapshot("1", 2, ltp=101.0, best_bid=100.5, best_ask=101.0, bid_qty=300, ask_qty=50))
    assert signal is not None
    assert signal.side == "BUY"
    assert signal.event.startswith("AGGRESSIVE_BUY")
    assert signal.score >= 35


def test_aggressive_sell_signal() -> None:
    detector = FlowDetector()
    detector.update(MarketSnapshot("2", 1, ltp=100.0, best_bid=100.0, best_ask=101.0, bid_qty=100, ask_qty=100))
    signal = detector.update(MarketSnapshot("2", 2, ltp=99.0, best_bid=99.0, best_ask=99.5, bid_qty=50, ask_qty=300))
    assert signal is not None
    assert signal.side == "SELL"


def test_small_noise_is_ignored() -> None:
    detector = FlowDetector()
    detector.update(MarketSnapshot("3", 1, ltp=100.0, best_bid=99.9, best_ask=100.1, bid_qty=100, ask_qty=100))
    signal = detector.update(MarketSnapshot("3", 2, ltp=100.01, best_bid=99.9, best_ask=100.1, bid_qty=101, ask_qty=100))
    assert signal is None
