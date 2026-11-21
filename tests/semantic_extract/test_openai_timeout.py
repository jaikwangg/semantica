"""OpenAIProvider passes a configured ``timeout`` to the OpenAI client.

Reasoning models behind OpenAI-compatible endpoints can take longer than the
SDK's 600s default, so a ``timeout`` given to the provider (directly or via
``create_provider`` / extractor kwargs) must reach ``OpenAI(...)`` (#2273).
"""

import pytest

pytest.importorskip("openai")

from semantica.semantic_extract.providers import OpenAIProvider, create_provider


def _read_timeout(provider: OpenAIProvider):
    timeout = provider.client.timeout
    return getattr(timeout, "read", timeout)


@pytest.mark.parametrize("base_url", [None, "http://localhost:8000/v1"])
def test_timeout_is_passed_to_client(base_url):
    provider = OpenAIProvider(api_key="fake", base_url=base_url, timeout=1800)

    assert _read_timeout(provider) == 1800


def test_create_provider_forwards_timeout():
    provider = create_provider(
        "openai", use_pool=False, api_key="fake", model="m", timeout=42.5
    )

    assert _read_timeout(provider) == 42.5


@pytest.mark.parametrize("timeout", [None, "unset"])
def test_sdk_default_timeout_when_not_configured(timeout):
    kwargs = {} if timeout == "unset" else {"timeout": timeout}
    provider = OpenAIProvider(api_key="fake", **kwargs)

    assert _read_timeout(provider) == 600
