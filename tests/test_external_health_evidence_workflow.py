from pathlib import Path


WORKFLOW = Path('.github/workflows/drcloud-os-external-health.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_external_health_monitor_persists_sanitized_history_artifact():
    text = _text()
    assert 'external-health-evidence.json' in text
    assert 'actions/upload-artifact@v4' in text
    assert 'retention-days: 90' in text
    assert 'drcloud-external-health-evidence-${{ github.run_id }}' in text
    for field in (
        'schema_version', 'observed_at', 'probe_status', 'failure_category',
        'http_status', 'contract_status', 'alert_action', 'sanitized',
    ):
        assert f'"{field}"' in text


def test_external_health_evidence_is_bounded_and_alert_delivery_is_measured():
    text = _text()
    for value in (
        '"SUCCESS"', '"FAILURE"', '"PROVEN"', '"UNPROVEN"',
        '"HTTP_STATUS"', '"INVALID_JSON"', '"CONTRACT"', '"NETWORK"',
        '"PENDING"', '"NOOP"', '"CREATED"', '"CLOSED"',
    ):
        assert value in text
    assert 'evidence["alert_action"] = os.environ["ALERT_ACTION"]' in text
    assert 'ALERT_OUTCOME: ${{ steps.alert.outcome }}' in text
    assert 'test "$ALERT_OUTCOME" = success' in text


def test_external_health_workflow_does_not_persist_response_or_secret_material():
    text = _text()
    assert 'response.read(' not in text
    assert 'response.headers' not in text
    assert 'Authorization' not in text
    assert 'secrets.' not in text
    assert 'payload' not in '\n'.join(
        line for line in text.splitlines() if 'json.dump(evidence' in line
    )
