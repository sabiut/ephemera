import pytest

from app.services.ai_deployment import AIDeploymentService, AIParseError


def _svc():
    return AIDeploymentService(deployment_service=None, github_service=None, kubernetes_service=None, provider=None)


def test_parses_plain_array():
    assert _svc()._parse_ai_response('[{"kind": "Service"}]') == [{"kind": "Service"}]


def test_strips_code_fences_and_unwraps_objects():
    svc = _svc()
    assert svc._parse_ai_response('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert svc._parse_ai_response('{"manifests": [{"a": 1}]}') == [{"a": 1}]


def test_fence_without_newline_does_not_crash():
    with pytest.raises(AIParseError):
        _svc()._parse_ai_response("```")


def test_object_without_array_key_is_an_error():
    with pytest.raises(AIParseError):
        _svc()._parse_ai_response('{"foo": 1}')
