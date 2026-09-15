from app.api.dashboard_filters import inject_filter_ui


def test_filters_injected_for_supported_dashboard() -> None:
    html = '<html><body><div id="notice">Notice</div></body></html>'
    out = inject_filter_ui(html, "/price-action")
    assert 'id="dashboardFilterPanel"' in out
    assert 'dfPattern' in out
    assert out.count('dashboardFilterPanel') == 1


def test_filters_not_injected_for_api_or_unknown_page() -> None:
    html = '<html><body><div id="notice">Notice</div></body></html>'
    assert inject_filter_ui(html, "/dashboard/data") == html
    assert inject_filter_ui(html, "/unknown") == html


def test_filters_are_idempotent() -> None:
    html = '<html><body><div id="notice">Notice</div></body></html>'
    once = inject_filter_ui(html, "/strategy")
    twice = inject_filter_ui(once, "/strategy")
    assert twice == once
