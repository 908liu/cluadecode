from __future__ import annotations

import dataclasses

import pytest

from app.core.net import OutboundUrlError, check_outbound_url
from app.services import upstream
from tests.conftest import auth_headers


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/v1",
        "http://localhost:9000/v1",
        "http://10.0.0.8/v1",
        "http://172.16.0.2/v1",
        "http://192.168.1.2/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:8000/v1",
        "http://0.0.0.0:8000/v1",
    ],
)
def test_outbound_url_blocks_private_addresses(url):
    with pytest.raises(OutboundUrlError):
        check_outbound_url(url, allow_private=False)


def test_outbound_url_allows_private_in_dev_and_allows_public():
    check_outbound_url("http://127.0.0.1:9000/v1", allow_private=True)
    check_outbound_url("https://8.8.8.8/v1", allow_private=False)


def test_outbound_allowlist():
    with pytest.raises(OutboundUrlError):
        check_outbound_url(
            "https://1.1.1.1/v1", allow_private=False, allowed_hosts=("8.8.8.8",)
        )
    check_outbound_url(
        "https://8.8.8.8/v1",
        allow_private=False,
        allowed_hosts=("8.8.8.8",),
    )


def test_upstream_guard_blocks_private_before_network():
    service = upstream.ServiceInfo(
        id=None, name="blocked", type="chat", base_url="http://127.0.0.1:1",
        api_key="k", model="m", timeout_seconds=1, dimensions=None,
        enabled=True, source="default", allow_private_hosts=False,
    )
    with pytest.raises(upstream.UpstreamError) as exc:
        upstream.chat_completion(service, [{"role": "user", "content": "ping"}])
    assert exc.value.error_type == "blocked"


def test_first_user_mode_closes_registration(app):
    client = app
    auth_headers(client, "alice")
    client.app.state.settings = dataclasses.replace(
        client.app.state.settings, registration_mode="first-user"
    )

    status = client.get("/api/v1/auth/registration-status").json()["data"]
    assert status["mode"] == "first-user"
    assert status["registration_allowed"] is False

    blocked = client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["code"] == 40301


def test_invite_mode_requires_matching_code(app):
    client = app
    client.app.state.settings = dataclasses.replace(
        client.app.state.settings,
        registration_mode="invite",
        registration_invite_code="letmein",
    )

    wrong = client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123", "invite_code": "nope"},
    )
    assert wrong.status_code == 403
    assert wrong.json()["code"] == 40302

    good = client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123", "invite_code": "letmein"},
    )
    assert good.status_code == 200, good.text