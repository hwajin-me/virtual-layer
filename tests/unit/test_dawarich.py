"""Dawarich API contracts, hostile input and credential-safe failure handling."""

import asyncio
import io
import json
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
from aiohttp import ClientError
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer import dawarich as api

pytestmark = pytest.mark.unit
CONFIG = {"url": "https://example.test/dawarich/", "api_key": "private-test-key"}


class Response:
    def __init__(self, payload=None, *, status=200, headers=None, body=None):
        self.status = status
        self.headers = headers or {}
        stream = io.BytesIO(json.dumps(payload).encode() if body is None else body)
        self.content = Mock(read=AsyncMock(side_effect=stream.read))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def point(age=0, **kwargs):
    return {
        "latitude": "37.5",
        "longitude": "127.0",
        "accuracy": 10,
        "timestamp": str((dt_util.utcnow() - timedelta(seconds=age)).timestamp()),
        **kwargs,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "https://user:secret@example.test"},
        {"url": "https://example.test?api_key=secret"},
        {"url": "https://example.test:99999"},
        {"url": "file:///tmp/points"},
        {"url": "https://example.test\n"},
        {"api_key": "key\r\nheader"},
        {"auth_mode": []},
        {"person_entity_id": []},
        {"person_entity_id": "sensor.person"},
        {"poll_interval": True},
        {"poll_interval": 15.5},
        {"poll_interval": 3601},
        {"history_limit": 0},
        {"history_limit": float("nan")},
        {"member": {}},
    ],
)
def test_rejects_invalid_configuration(overrides):
    with pytest.raises(vol.Invalid):
        api.normalize_config({**CONFIG, **overrides})


@pytest.mark.parametrize(
    "raw", [None, True, [], {}, "nan", "Infinity", 10**1000, "invalid"]
)
def test_invalid_measurement_time(raw):
    assert api.point_time({"timestamp": raw}) is None


def test_numeric_and_iso_measurement_times():
    expected = api.point_time({"timestamp": 1_700_000_000})
    for raw in (
        "1700000000",
        1_700_000_000_000,
        "2023-11-14T22:13:20Z",
        "2023-11-14T22:13:20",
    ):
        assert api.point_time({"timestamp": raw}) == expected


async def test_points_filter_sort_bound_history_and_follow_last_visit_page():
    recent = point(2, speed=4, raw_data={"api_key": "secret"})
    previous = point(10)
    session = Session(
        Response([previous, point(1, latitude=91), point(-100), recent, point(20)]),
        Response(
            [{"name": "Old", "started_at": point(1000)["timestamp"]}],
            headers={"X-Total-Pages": "3"},
        ),
        Response(
            [{"name": "Office", "started_at": point(20)["timestamp"], "ended_at": None}]
        ),
    )
    result = await api.DawarichClient(
        session, {**CONFIG, "history_limit": 2}
    ).async_fetch()
    assert result.point["speed"] == 4
    assert len(result.history) == 2
    assert result.history[0]["timestamp"] > result.history[1]["timestamp"]
    assert result.point["latitude"] == 37.5
    assert result.visit["name"] == "Office"
    assert "raw_data" not in result.point
    assert session.calls[0][0] == "https://example.test/dawarich/api/v1/points"
    assert session.calls[0][1]["params"] == {"page": 1, "order": "desc", "per_page": 2}
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer private-test-key"
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[2][1]["params"]["page"] == 3
    assert all(call[1]["ssl"] is False for call in session.calls)
    assert {"start_at", "end_at"} <= session.calls[1][1]["params"].keys()


@pytest.mark.parametrize("member", ["alex@example.test", "42"])
async def test_family_uses_explicit_identity_and_never_account_visits(member):
    session = Session(
        Response(
            {
                "locations": [
                    {
                        **point(1),
                        "user_id": 99,
                        "email": "other@example.test",
                        "latitude": 50,
                    },
                    {**point(10), "user_id": 42, "email": "alex@example.test"},
                ]
            }
        )
    )
    result = await api.DawarichClient(
        session, {**CONFIG, "member": member}
    ).async_fetch()
    assert result.point["latitude"] == 37.5
    assert result.visit is None
    assert len(session.calls) == 1
    assert session.calls[0][0].endswith("/families/locations")
    assert session.calls[0][1]["ssl"] is False
    assert "email" not in result.point


@pytest.mark.parametrize(
    ("members", "code"),
    [
        ([{**point(), "email": "other@example.test"}], "member_not_found"),
        ([{**point(), "email": "alex@example.test"}] * 2, "ambiguous_member"),
    ],
)
async def test_family_missing_or_ambiguous_never_falls_back(members, code):
    client = api.DawarichClient(
        Session(Response({"locations": members})),
        {**CONFIG, "member": "alex@example.test"},
    )
    with pytest.raises(api.DawarichError, match=code):
        await client.async_fetch()


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (Response(status=401), "invalid_auth"),
        (Response(status=403), "invalid_auth"),
        (Response(status=302), "http_error"),
        (Response(status=500), "http_error"),
        (Response(body=b"not json"), "invalid_response"),
        (Response(body=b" " * (api.MAX_RESPONSE_BYTES + 1)), "invalid_response"),
        (
            ClientError("https://example.test?api_key=private-test-key"),
            "cannot_connect",
        ),
        (asyncio.TimeoutError(), "cannot_connect"),
        (Response([]), "no_points"),
    ],
)
async def test_safe_failure_codes(response, code):
    with pytest.raises(api.DawarichError) as error:
        await api.DawarichClient(
            Session(response), {**CONFIG, "auth_mode": "query"}
        ).async_fetch()
    assert str(error.value) == code
    assert "private-test-key" not in str(error.value)


async def test_visit_failure_preserves_point_and_query_auth():
    session = Session(Response([point()]), Response(status=404))
    result = await api.DawarichClient(
        session, {**CONFIG, "auth_mode": "query"}
    ).async_fetch()
    assert result.point["latitude"] == 37.5
    assert result.visit_error == "http_error"
    assert session.calls[0][1]["params"]["api_key"] == "private-test-key"
    assert "Authorization" not in session.calls[0][1]["headers"]
    assert all(call[1]["ssl"] is False for call in session.calls)
