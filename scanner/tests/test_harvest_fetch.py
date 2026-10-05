"""Offline tests for engine/harvest.fetch 403 strategy (P3-12, live
2026-10-05: NSE 403-blocked both the Render server and the sandbox IP)."""
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

import harvest                                  # noqa: E402


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content

    @property
    def text(self):
        return self.content.decode(errors="replace")


def _no_sleep(monkeypatch):
    monkeypatch.setattr(harvest.time, "sleep", lambda s: None)


def test_200_passthrough(monkeypatch):
    _no_sleep(monkeypatch)
    body = b"SYMBOL, SERIES\r\nRELIANCE, EQ\r\n"
    monkeypatch.setattr(harvest.requests, "get",
                        lambda u, headers=None, timeout=None: _Resp(200, body))
    assert harvest.fetch("http://nse/cash.csv") == body


def test_404_is_none_not_posted(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(harvest.requests, "get",
                        lambda u, headers=None, timeout=None: _Resp(404))
    assert harvest.fetch("http://nse/cash.csv") is None


def test_soft_error_page_is_none(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(harvest.requests, "get",
                        lambda u, headers=None, timeout=None: _Resp(200, b"<html>err</html>"))
    assert harvest.fetch("http://nse/cash.csv") is None


def test_403_transient_recovers(monkeypatch):
    """WAF storm that clears after retries -> normal bytes, no raise."""
    _no_sleep(monkeypatch)
    seq = [_Resp(403), _Resp(403), _Resp(200, b"SYMBOL,ok\r\n1\r\n")]
    monkeypatch.setattr(harvest.requests, "get",
                        lambda u, headers=None, timeout=None: seq.pop(0))
    assert harvest.fetch("http://nse/cash.csv") == b"SYMBOL,ok\r\n1\r\n"


def _jina_router(direct_status=403, wrapped="Title: t\n\nURL Source: u\n\n"
                 "Published Time: p\n\nMarkdown Content:\nSYMBOL, SERIES\n"
                 "RELIANCE, EQ, 100\n"):
    def get(u, headers=None, timeout=None):
        if u.startswith("https://r.jina.ai/"):
            return _Resp(200, wrapped.encode())
        return _Resp(direct_status)
    return get


def test_403_persistent_proxy_csv_fallback(monkeypatch):
    """Hard block + text CSV via r.jina.ai -> wrapper stripped, CSV bytes."""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(harvest.requests, "get", _jina_router())
    out = harvest.fetch("http://nse/sec_bhavdata_full.csv")
    assert out == b"SYMBOL, SERIES\nRELIANCE, EQ, 100\n"


def test_403_persistent_binary_no_fallback_raises(monkeypatch):
    """Hard block + zip (proxy can't return binaries) -> LOUD raise."""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(harvest.requests, "get", _jina_router(wrapped="Title: t\n\nURL Source: u\n\n422 json junk"))
    with pytest.raises(RuntimeError, match="403 Access Denied"):
        harvest.fetch("http://nse/BhavCopy_FO.zip")


def test_403_persistent_proxy_down_raises(monkeypatch):
    """Hard block + proxy unreachable -> LOUD raise (never 'file absent')."""
    _no_sleep(monkeypatch)

    def get(u, headers=None, timeout=None):
        if u.startswith("https://r.jina.ai/"):
            raise requests.RequestException("proxy down")
        return _Resp(403)
    monkeypatch.setattr(harvest.requests, "get", get)
    with pytest.raises(RuntimeError, match="403 Access Denied"):
        harvest.fetch("http://nse/cash.csv")


def test_network_dead_returns_none(monkeypatch):
    """Total network outage -> None (not-posted-yet ladder; eventually loud
    via the market-open check at ladder end)."""
    _no_sleep(monkeypatch)

    def get(u, headers=None, timeout=None):
        raise requests.RequestException("down")
    monkeypatch.setattr(harvest.requests, "get", get)
    assert harvest.fetch("http://nse/cash.csv") is None
