import base64

import pytest
import requests

from hdl_toolkit import HdlClient, HdlError, derive_status


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    """Replays queued responses and records requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.auth = None

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)


def make_client(responses):
    session = FakeSession(responses)
    clock = iter(range(0, 10_000, 10))
    client = HdlClient(
        "https://hcm.example.com/",
        "user",
        "pass",
        session=session,
        sleep=lambda _s: None,
        clock=lambda: next(clock),
    )
    return client, session


@pytest.mark.parametrize(
    "data_set, expected",
    [
        ({"ImportStatusCode": "IN_PROGRESS"}, None),
        ({"LoadStatusCode": "SUCCESS"}, "SUCCESS"),
        ({"LoadStatusCode": "SUCCESS", "ObjectLoadErrorCount": 2}, "FAILED"),
        ({"LoadStatusCode": "SUCCESS", "ObjectUnprocessedCount": 1}, "WARNING"),
        ({"ImportStatusCode": "ERROR"}, "FAILED"),
        ({"DataSetStatusCode": "CANCELLED"}, "FAILED"),
        ({"LoadStatusCode": "WARNING"}, "WARNING"),
    ],
)
def test_derive_status(data_set, expected):
    assert derive_status(data_set) == expected


# Payload seen from a real pod after an IMPORT_ONLY submission.
IMPORTED = {
    "TransferStatusCode": "SUCCESS",
    "ImportStatusCode": "SUCCESS",
    "LoadStatusCode": "UNPROCESSED",
}


@pytest.mark.parametrize(
    "data_set, expected",
    [
        (
            {
                "TransferStatusCode": "SUCCESS",
                "ImportStatusCode": "IN_PROGRESS",
                "LoadStatusCode": "NOT_READY",
            },
            None,
        ),
        (IMPORTED, "SUCCESS"),
        ({**IMPORTED, "FileLineImportErrorCount": 3}, "FAILED"),
        ({**IMPORTED, "ImportStatusCode": "WARNING"}, "WARNING"),
        ({**IMPORTED, "ImportStatusCode": "ERROR"}, "FAILED"),
    ],
)
def test_derive_status_import_only(data_set, expected):
    assert derive_status(data_set, import_only=True) == expected


def test_import_and_load_keeps_waiting_after_import():
    # Same payload mid-way through IMPORT_AND_LOAD is not final yet.
    assert derive_status(IMPORTED) is None


def test_submit_import_only_stops_after_import():
    client, session = make_client(
        [
            FakeResponse(payload={"result": {"ContentId": "C"}}),
            FakeResponse(payload={"result": {"RequestId": 64869600}}),
            FakeResponse(payload={**IMPORTED, "ImportStatusCode": "IN_PROGRESS"}),
            FakeResponse(payload=IMPORTED),
            FakeResponse(payload={"items": []}),
        ]
    )
    result = client.submit(b"z", "Worker.zip", file_action="IMPORT_ONLY")
    assert result.status == "SUCCESS"
    assert session.calls[1][2]["json"]["fileAction"] == "IMPORT_ONLY"
    assert session.responses == []


def test_submit_happy_path():
    client, session = make_client(
        [
            FakeResponse(payload={"result": {"ContentId": "UCMFA001"}}),
            FakeResponse(payload={"result": {"RequestId": 42}}),
            FakeResponse(404),  # not registered yet: transient
            FakeResponse(payload={"ImportStatusCode": "IN_PROGRESS"}),
            FakeResponse(payload={"LoadStatusCode": "SUCCESS", "ObjectSuccessCount": 3}),
            FakeResponse(payload={"items": [{"MessageTypeCode": "INFO", "MessageText": "ok"}]}),
        ]
    )
    result = client.submit(b"zipbytes", "Worker.zip", data_set_name="TEST")

    assert result.ok
    assert (result.content_id, result.request_id) == ("UCMFA001", 42)
    assert result.messages[0]["message"] == "ok"

    upload = session.calls[0]
    assert upload[1] == (
        "https://hcm.example.com/hcmRestApi/resources/11.13.18.05"
        "/dataLoadDataSets/action/uploadFile"
    )
    assert base64.b64decode(upload[2]["json"]["content"]) == b"zipbytes"
    assert upload[2]["headers"]["Content-Type"] == "application/vnd.oracle.adf.action+json"
    submit = session.calls[1][2]["json"]
    assert submit == {
        "contentId": "UCMFA001",
        "fileAction": "IMPORT_AND_LOAD",
        "deleteSourceFileFlag": "N",
        "dataSetName": "TEST",
    }
    assert session.auth == ("user", "pass")


def test_submit_without_wait():
    client, session = make_client(
        [
            FakeResponse(payload={"result": {"ContentId": "C"}}),
            FakeResponse(payload={"result": {"RequestId": 7}}),
        ]
    )
    result = client.submit(b"z", "Worker.zip", wait=False)
    assert result.status == "SUBMITTED"
    assert len(session.calls) == 2


def test_upload_error_raises():
    client, _ = make_client([FakeResponse(401, {"detail": "unauthorized"})])
    with pytest.raises(HdlError, match="uploadFile failed \\(401\\)"):
        client.upload_file(b"z", "Worker.zip")


def test_wait_times_out():
    client, _ = make_client([FakeResponse(payload={"ImportStatusCode": "IN_PROGRESS"})] * 50)
    with pytest.raises(TimeoutError):
        client.wait(1, poll_interval=1, timeout=30)


def test_message_failure_does_not_change_result():
    client, _ = make_client(
        [
            FakeResponse(payload={"result": {"ContentId": "C"}}),
            FakeResponse(payload={"result": {"RequestId": 1}}),
            FakeResponse(payload={"LoadStatusCode": "SUCCESS"}),
            FakeResponse(500),
        ]
    )
    result = client.submit(b"z", "Worker.zip")
    assert result.status == "SUCCESS"
    assert result.messages == []
