"""
The PR comment must not carry provider error text. The reason for an AI
fallback goes to the logs; the comment only says the converter ran.
"""

from app.tasks.environment import _deployment_summary

RAW_ERROR = (
    "Anthropic API error: Error code: 400 - {'type': 'error', 'message': "
    "'Your credit balance is too low', 'request_id': 'req_011CfFt3ozAEwmZc1wWZbtx5'}"
)


def _result(**overrides):
    base = {
        "compose_found": True,
        "services": ["web", "echo"],
        "service_urls": {"web": "https://ns-web.example", "echo": "https://ns-echo.example"},
    }
    base.update(overrides)
    return base


def test_fallback_note_is_generic_and_hides_the_reason():
    summary = _deployment_summary(_result(ai_fallback_reason=RAW_ERROR))
    assert "AI manifest generation unavailable, used the compose converter" in summary
    assert "req_011" not in summary
    assert "credit balance" not in summary
    assert "Anthropic" not in summary


def test_disabled_ai_adds_no_note():
    summary = _deployment_summary(_result(ai_fallback_reason="AI deployment disabled"))
    assert "Note" not in summary


def test_ai_plan_is_shown_when_ai_generated(caplog):
    summary = _deployment_summary(_result(ai_generated=True, ai_plan="two deployments, one ingress"))
    assert "AI Deployment Plan" in summary
    assert "two deployments, one ingress" in summary
    assert "unavailable" not in summary


def test_services_and_skipped_are_listed():
    summary = _deployment_summary(_result(skipped_services=["builder"]))
    assert "- **web**: https://ns-web.example" in summary
    assert "`builder`" in summary
