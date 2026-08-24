from pathlib import Path


def test_external_health_monitor_is_public_read_only_and_fail_closed():
    workflow = Path(".github/workflows/drcloud-os-external-health.yml").read_text()
    assert 'cron: "*/15 * * * *"' in workflow
    assert "HEALTH_URL: https://osdrcloud.fr/health" in workflow
    assert "contents: read" in workflow
    assert "secrets." not in workflow
    assert 'probe_status="SUCCESS"' in workflow or 'probe_status="FAILURE"' in workflow
    assert '"probe_status": "FAILURE"' in workflow
    assert '"contract_status": "UNPROVEN"' in workflow
    assert 'payload.get("status") == "ok"' in workflow
    assert 'payload.get("database") == "ok"' in workflow
    assert 'test "$PROBE_OUTCOME" = success' in workflow
    assert 'test "$ALERT_OUTCOME" = success' in workflow
