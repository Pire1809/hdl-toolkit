"""Client for the Oracle HCM ``dataLoadDataSets`` REST resource.

Flow: upload a base64 ZIP (``uploadFile``) -> get a ``ContentId`` ->
submit it (``createFileDataSet``) -> get a ``RequestId`` -> poll the data
set until it reaches a final business status -> read its messages.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

from .package import to_base64

log = logging.getLogger(__name__)

DEFAULT_API_VERSION = "11.13.18.05"
ACTION_CONTENT_TYPE = "application/vnd.oracle.adf.action+json"

STATUS_FIELDS = (
    "RequestId,DataSetId,ContentId,DataSetName,DataSetStatusCode,"
    "DataSetStatusMeaning,TransferStatusCode,ImportStatusCode,LoadStatusCode,"
    "ImportPercentageComplete,LoadPercentageComplete,FileLineTotalCount,"
    "FileLineImportErrorCount,FileLineImportSuccessCount,ObjectTotalCount,"
    "ObjectLoadErrorCount,ObjectRollbackErrorCount,ObjectSuccessCount,"
    "ObjectUnprocessedCount,CreationDate,LastUpdateDate"
)

FAILURE_CODES = frozenset({"ERROR", "FAILED", "CANCELED", "CANCELLED", "TERMINATED"})


class HdlError(RuntimeError):
    """Raised when Oracle rejects a request."""


def derive_status(data_set: dict[str, Any]) -> str | None:
    """Map a data set payload to SUCCESS / WARNING / FAILED.

    Returns ``None`` while Oracle is still processing. A load that "succeeds"
    with object-level errors is reported as FAILED, and one that leaves
    objects unprocessed as WARNING, because Oracle's own status code alone
    hides both cases.
    """

    def code(key: str) -> str:
        return str(data_set.get(key) or "").upper()

    def count(key: str) -> int:
        return int(data_set.get(key) or 0)

    transfer, import_, load = (
        code("TransferStatusCode"),
        code("ImportStatusCode"),
        code("LoadStatusCode"),
    )
    overall = code("DataSetStatusCode")

    if {transfer, import_, load} & FAILURE_CODES or "ERROR" in overall or "CANCEL" in overall:
        return "FAILED"

    if load == "SUCCESS":
        errors = (
            count("FileLineImportErrorCount")
            + count("ObjectLoadErrorCount")
            + count("ObjectRollbackErrorCount")
        )
        if errors:
            return "FAILED"
        if count("ObjectUnprocessedCount"):
            return "WARNING"
        return "SUCCESS"

    if load == "WARNING" or "WARNING" in overall:
        return "WARNING"

    return None


@dataclass
class LoadResult:
    status: str
    request_id: int | str
    content_id: str | None
    data_set: dict[str, Any]
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS"


class HdlClient:
    def __init__(
        self,
        instance_url: str,
        username: str,
        password: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = 300,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = f"{instance_url.rstrip('/')}/hcmRestApi/resources/{api_version}"
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.auth = (username, password)
        self._sleep = sleep
        self._clock = clock

    # -- low level ---------------------------------------------------------

    def _action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/dataLoadDataSets/action/{action}",
            json=payload,
            headers={"Content-Type": ACTION_CONTENT_TYPE, "Accept": "application/json"},
            timeout=self.timeout,
        )
        if not response.ok:
            raise HdlError(f"{action} failed ({response.status_code}): {response.text[:500]}")
        return response.json()["result"]

    def upload_file(self, zip_bytes: bytes, file_name: str) -> str:
        """Upload a ZIP to UCM and return its ``ContentId``."""
        result = self._action(
            "uploadFile", {"fileName": file_name, "content": to_base64(zip_bytes)}
        )
        return result["ContentId"]

    def create_data_set(
        self,
        content_id: str,
        data_set_name: str | None = None,
        *,
        file_action: str = "IMPORT_AND_LOAD",
        delete_source_file: bool = False,
    ) -> int | str:
        """Submit an uploaded file for import (and load); return the ``RequestId``."""
        payload: dict[str, Any] = {
            "contentId": content_id,
            "fileAction": file_action,
            "deleteSourceFileFlag": "Y" if delete_source_file else "N",
        }
        if data_set_name:
            payload["dataSetName"] = data_set_name
        return self._action("createFileDataSet", payload)["RequestId"]

    def get_data_set(self, request_id: int | str) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/dataLoadDataSets/{request_id}",
            params={"onlyData": "true", "fields": STATUS_FIELDS},
            headers={"Accept": "application/json"},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def get_messages(self, request_id: int | str, limit: int = 25) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.base_url}/dataLoadDataSets/{request_id}/child/messages",
            params={"onlyData": "true", "limit": limit},
            headers={"Accept": "application/json"},
            timeout=60,
        )
        response.raise_for_status()
        return [
            {
                "type": item.get("MessageTypeCode"),
                "process": item.get("OriginatingProcessCode"),
                "message": item.get("MessageText"),
            }
            for item in response.json().get("items", [])
        ]

    # -- high level --------------------------------------------------------

    def wait(
        self,
        request_id: int | str,
        *,
        poll_interval: float = 30,
        timeout: float = 900,
        on_poll: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Poll until the data set is final; return ``(status, data_set)``.

        Transient HTTP errors (including the 404 Oracle returns for a few
        seconds after submission) are logged and retried.
        """
        deadline = self._clock() + timeout
        last: dict[str, Any] = {}
        while True:
            self._sleep(poll_interval)
            try:
                last = self.get_data_set(request_id)
            except requests.RequestException as error:
                log.warning("Transient error polling RequestId=%s: %s", request_id, error)
            else:
                if on_poll:
                    on_poll(last)
                status = derive_status(last)
                if status:
                    return status, last
            if self._clock() >= deadline:
                raise TimeoutError(
                    f"RequestId={request_id} not final after {timeout:.0f}s "
                    f"(load={last.get('LoadStatusCode')}, "
                    f"import={last.get('ImportStatusCode')})"
                )

    def submit(
        self,
        zip_bytes: bytes,
        file_name: str,
        *,
        data_set_name: str | None = None,
        file_action: str = "IMPORT_AND_LOAD",
        wait: bool = True,
        poll_interval: float = 30,
        timeout: float = 900,
        on_poll: Callable[[dict[str, Any]], None] | None = None,
    ) -> LoadResult:
        """Upload, submit and (optionally) wait for a ZIP in one call."""
        content_id = self.upload_file(zip_bytes, file_name)
        log.info("Uploaded %s -> ContentId=%s", file_name, content_id)
        request_id = self.create_data_set(content_id, data_set_name, file_action=file_action)
        log.info("Submitted ContentId=%s -> RequestId=%s", content_id, request_id)
        if not wait:
            return LoadResult("SUBMITTED", request_id, content_id, {})

        status, data_set = self.wait(
            request_id, poll_interval=poll_interval, timeout=timeout, on_poll=on_poll
        )
        try:
            messages = self.get_messages(request_id)
        except requests.RequestException as error:
            # The load result is already final; a failed message lookup
            # must not change it.
            log.warning("Could not read messages for RequestId=%s: %s", request_id, error)
            messages = []
        return LoadResult(status, request_id, content_id, data_set, messages)
