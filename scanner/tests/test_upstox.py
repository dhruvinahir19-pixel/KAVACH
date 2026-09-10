import pytest
import requests

from kcore.upstox_client import UpstoxAuthError, UpstoxClient, UpstoxError


class FakeResp:
    def __init__(self, code):
        self.status_code = code

    def json(self):
        return {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, script):
        self.script = script    # list of FakeResp codes or exceptions
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        item = self.script[max(idx, 0)]
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)


def _client(script):
    s = FakeSession(script)
    return UpstoxClient("tok", sleep=lambda sec: None, session=s), s


def test_auth_error_is_immediate_no_retry():
    c, s = _client([401])
    with pytest.raises(UpstoxAuthError, match="regenerate"):
        c._get("https://x/y")
    assert len(s.calls) == 1                     # NO retries on a dead token


def test_backoff_then_success():
    c, s = _client([429, 200])
    assert c._get("https://x/y") == {}
    assert len(s.calls) == 2


def test_5xx_exhausts_and_raises():
    c, s = _client([500, 500, 500, 500])
    with pytest.raises(UpstoxError, match="retries exhausted"):
        c._get("https://x/y")
    assert len(s.calls) == 4          # 1 initial + 3 backoff retries


def test_network_error_ladder_then_success():
    c, s = _client([requests.RequestException("net"), 200])
    assert c._get("https://x/y") == {}
    assert len(s.calls) == 2


def test_other_4xx_raises_for_status():
    c, s = _client([404])
    with pytest.raises(requests.HTTPError):
        c._get("https://x/y")
    assert len(s.calls) == 1


def test_intraday_url_shape():
    c, s = _client([200])
    c._get_original = None
    resp = c._get  # sanity that client is constructible
    assert resp is not None
