"""Static hygiene checks: the 'no silence' rules, mechanized."""
from pathlib import Path

KCORE = Path(__file__).resolve().parents[1] / "kcore"
SOURCES = {f.name: f.read_text() for f in KCORE.glob("*.py")}


def test_no_bare_except():
    for name, src in SOURCES.items():
        assert "except:" not in src, f"bare except in {name}"


def test_datetime_now_only_in_clock():
    for name, src in SOURCES.items():
        if name == "clock.py":
            continue
        assert "datetime.now(" not in src, f"direct now() in {name} — use kcore.clock"


def test_no_hardcoded_secrets():
    for name, src in SOURCES.items():
        low = src.lower()
        assert "npg_" not in src, f"Neon credential fragment in {name}"
        assert "ghp_" not in src, f"GitHub token fragment in {name}"
        assert "bot_token=\"" not in low and "token=\"" not in low, f"hardcoded token in {name}"


def test_health_never_imports_store_at_module_level_check():
    """app.py /health must stay DB-free: verify no Neon query in the health handler."""
    src = SOURCES["app.py"]
    h = src.split('def health')[1].split("return")[0] if "def health" in src else ""
    assert "neon" not in h.lower() and "conn" not in h.lower()
