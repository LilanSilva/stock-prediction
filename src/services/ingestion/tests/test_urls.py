from ingestion.urls import canonicalize_url


def test_strips_tracking_params_and_fragment() -> None:
    url = "https://www.di.se/nyheter/artikel?utm_source=x&utm_medium=y&id=42#top"
    assert canonicalize_url(url) == "https://www.di.se/nyheter/artikel?id=42"


def test_lowercases_scheme_and_host_and_drops_default_port() -> None:
    url = "HTTPS://WWW.DN.SE:443/rss/"
    assert canonicalize_url(url) == "https://www.dn.se/rss"


def test_sorts_query_and_keeps_meaningful_params() -> None:
    url = "https://svd.se/a?b=2&a=1&fbclid=abc"
    assert canonicalize_url(url) == "https://svd.se/a?a=1&b=2"


def test_equivalent_urls_canonicalize_equal() -> None:
    a = "https://example.com/story/?utm_campaign=z"
    b = "https://example.com/story"
    assert canonicalize_url(a) == canonicalize_url(b)


def test_non_default_port_is_preserved() -> None:
    assert canonicalize_url("http://example.com:8080/x") == "http://example.com:8080/x"
