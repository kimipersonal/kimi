"""Tests for the browser service — SSRF prevention and URL handling."""


from app.services.browser_service import _is_internal_url


class TestSSRFPrevention:
    def test_blocks_localhost(self):
        assert _is_internal_url("http://localhost/admin") is True
        assert _is_internal_url("http://127.0.0.1:8080") is True

    def test_blocks_private_ips(self):
        assert _is_internal_url("http://192.168.1.1") is True
        assert _is_internal_url("http://10.0.0.1/api") is True
        assert _is_internal_url("http://172.16.0.1") is True

    def test_blocks_loopback_ipv6(self):
        assert _is_internal_url("http://[::1]/") is True

    def test_blocks_cloud_metadata(self):
        assert _is_internal_url("http://169.254.169.254/metadata") is True
        assert _is_internal_url("http://metadata.google.internal/") is True

    def test_blocks_non_http_schemes(self):
        assert _is_internal_url("file:///etc/passwd") is True
        assert _is_internal_url("ftp://example.com") is True

    def test_allows_public_urls(self):
        assert _is_internal_url("https://example.com") is False
        assert _is_internal_url("https://google.com/search?q=test") is False
        assert _is_internal_url("http://news.ycombinator.com") is False

    def test_blocks_empty_and_malformed(self):
        assert _is_internal_url("") is True
        assert _is_internal_url("not-a-url") is True
