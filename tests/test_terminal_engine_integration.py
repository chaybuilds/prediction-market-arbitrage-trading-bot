"""Regression coverage for the actual fork-powered integration, no vendor key required."""
from fastapi.testclient import TestClient

from api.engine_fixtures import CASES, evaluate_fixtures
from api.index import app

def test_actual_pricer_and_strategy_run_for_every_fixture():
    result = evaluate_fixtures()
    assert result["synthetic"] is True
    assert result["execution_enabled"] is False
    assert result["source"] == "FORK_ENGINE_WITH_SYNTHETIC_INPUTS"
    assert len(result["opportunities"]) == len(CASES)
    for row in result["opportunities"]:
        assert row["source"] == "FORK_FIXTURE"
        assert row["source_code"] == ["src/arbitrage.py:Pricer", "src/strategy.py:Strategy"]
        assert row["evidence_status"] == "SIMULATED_UNVERIFIED"
        assert row["liquidity_verified"] is False
        assert 0 < row["yes_price"] < 1 and 0 < row["no_price"] < 1
        assert row["edge"] == round(1 - row["yes_price"] - row["no_price"] - row["fees"], 4)

def test_public_status_safe_even_without_keys(monkeypatch):
    monkeypatch.delenv("PUBLIC_SCANNER_ENABLED", raising=False)
    client = TestClient(app)
    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["scanner_ready"] is False
    assert status.json()["execution_enabled"] is False
    fixture = client.get("/api/engine/fixtures")
    assert fixture.status_code == 200
    assert fixture.json()["synthetic"] is True
    assert fixture.json()["execution_enabled"] is False
    engine = client.get("/api/engine/status")
    assert engine.status_code == 200
    assert engine.json()["fork_pricing"]["integrated"] is True
    assert engine.json()["nexus_worldline"]["runtime_connected"] is False
    assert engine.json()["execution_enabled"] is False

def test_live_scanner_fails_closed_without_data_key(monkeypatch):
    monkeypatch.delenv("PUBLIC_SCANNER_ENABLED", raising=False)
    response = TestClient(app).get("/api/opportunities", headers={"origin": "https://chay-trades-arb-terminal.vercel.app"})
    assert response.status_code == 503


def test_nexus_gateway_abstains_when_not_configured(monkeypatch):
    import api.index as route
    origin = "https://chay-trades-arb-terminal.vercel.app"
    monkeypatch.setattr(route, "allowed_origins", [origin])
    monkeypatch.delenv("NEXUS_ADVISORY_URL", raising=False)
    monkeypatch.delenv("NEXUS_ADVISORY_SECRET", raising=False)
    response = TestClient(app).get("/api/engine/intelligence/FX-BTC-001", headers={"origin": origin})
    assert response.status_code == 503

def test_nexus_gateway_never_exposes_token_or_orders(monkeypatch):
    import api.index as route
    import httpx
    origin = "https://chay-trades-arb-terminal.vercel.app"
    monkeypatch.setattr(route, "allowed_origins", [origin])
    monkeypatch.setenv("NEXUS_ADVISORY_URL", "https://example.org")
    monkeypatch.setenv("NEXUS_ADVISORY_SECRET", "example-test-secret-not-for-production-xxxx")
    def fake_post(url, json, headers, timeout, follow_redirects):
        assert headers["Authorization"].startswith("Bearer ")
        assert json["source"] == "FORK_FIXTURE"
        assert json["rules"] == "synthetic_assumed"
        assert "secret" not in str(json).lower()
        assert follow_redirects is False
        return type("Response", (), {"raise_for_status": lambda self: None, "json": lambda self: {
            "schema":"nexus.oracle.prediction-arbitrage.v1","authority":"ADVISORY_ONLY",
            "executionEnabled":False,"worldline":{"schema":"nexus.worldline.forecast.v1"}
        }})()
    monkeypatch.setattr(httpx, "post", fake_post)
    response=TestClient(app).get("/api/engine/intelligence/FX-BTC-001", headers={"origin": origin})
    assert response.status_code == 200
    data=response.json()
    assert data["execution_enabled"] is False
    assert "Bearer" not in str(data)
