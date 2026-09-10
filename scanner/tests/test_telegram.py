import requests

from kcore import telegram


class FakeResp:
    def __init__(self, code):
        self.status_code = code


def _patch(monkeypatch, codes, exc=None):
    calls = []
    sleeps = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        code = codes[len(calls) - 1] if len(calls) <= len(codes) else codes[-1]
        if exc and len(calls) > 1:
            raise requests.RequestException("boom")
        return FakeResp(code)

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    monkeypatch.setattr(telegram.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_send_ok(monkeypatch):
    calls, _ = _patch(monkeypatch, [200])
    assert telegram.send("hi", "tok", "123") is True
    assert len(calls) == 1


def test_send_429_retries_once(monkeypatch):
    calls, sleeps = _patch(monkeypatch, [429, 200])
    assert telegram.send("hi", "tok", "123") is True
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_send_hard_fail_no_retry(monkeypatch):
    calls, _ = _patch(monkeypatch, [400])
    assert telegram.send("hi", "tok", "123") is False
    assert len(calls) == 1


def test_send_network_exception_false(monkeypatch):
    calls, _ = _patch(monkeypatch, [200], exc=True)

    def raising(url, json=None, timeout=None):
        raise requests.RequestException("network down")

    monkeypatch.setattr(telegram.requests, "post", raising)
    assert telegram.send("hi", "tok", "123") is False


def test_alert_prefixes_severity(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["payload"] = json
        return FakeResp(200)

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    assert telegram.alert("disk full", "tok", "123", severity="ERROR") is True
    assert seen["payload"]["text"].startswith("[ERROR] KAVACH: disk full")
