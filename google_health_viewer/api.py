from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from datetime import time as clock
from typing import Any

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

from .constants import API_BASE, DataTypeSpec


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.details = details


class GoogleHealthClient:
    def __init__(self, credentials: Credentials) -> None:
        self.credentials = credentials
        self.session = requests.Session()

    def _ensure_token(self) -> None:
        if not self.credentials.valid or self.credentials.expired:
            if not self.credentials.refresh_token:
                raise ApiError(401, "L'accesso Google è scaduto: autenticati nuovamente.")
            try:
                self.credentials.refresh(GoogleAuthRequest())
            except Exception as exc:
                raise ApiError(401, f"Impossibile rinnovare l'accesso Google: {exc}") from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        cancel: Callable[[], bool] | None = None,
        max_attempts: int = 3,
        read_timeout: float = 30,
    ) -> dict[str, Any]:
        self._ensure_token()
        url = f"{API_BASE}/{path.lstrip('/')}"
        for attempt in range(max_attempts):
            if cancel and cancel():
                raise ApiError(499, "Operazione annullata.")
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers={
                        "Authorization": f"Bearer {self.credentials.token}",
                        "Accept": "application/json",
                    },
                    timeout=(8, read_timeout),
                )
            except requests.RequestException as exc:
                if attempt == max_attempts - 1:
                    raise ApiError(0, f"Errore di rete: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if (
                response.status_code in {429, 500, 502, 503, 504}
                and attempt < max_attempts - 1
            ):
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(min(delay, 30))
                continue
            if response.status_code >= 400:
                try:
                    body = response.json()
                    error = body.get("error", body)
                    message = error.get("message", response.text)
                except (ValueError, AttributeError):
                    body = response.text
                    message = response.text
                raise ApiError(response.status_code, message, body)
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise ApiError(502, "La Google Health API ha restituito JSON non valido.") from exc
        raise ApiError(0, "Richiesta non completata.")

    @staticmethod
    def _date_filter(spec: DataTypeSpec, start: date, end: date) -> str | None:
        if not spec.filter_field:
            return None
        field = spec.filter_field
        exclusive_end = end + timedelta(days=1)
        if spec.record_type == "daily" or ".civil_" in field:
            lower = start.isoformat()
            upper = exclusive_end.isoformat()
        else:
            lower = datetime.combine(start, clock.min).astimezone().isoformat()
            upper = datetime.combine(exclusive_end, clock.min).astimezone().isoformat()
        if spec.key == "electrocardiogram":
            return f'{field} >= "{lower}"'
        return f'{field} >= "{lower}" AND {field} < "{upper}"'

    def iter_data_pages(
        self,
        spec: DataTypeSpec,
        start: date,
        end: date,
        cancel: Callable[[], bool] | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        page_token = None
        seen_tokens: set[str] = set()
        pages = 0
        while True:
            pages += 1
            if pages > 1000:
                raise ApiError(508, f"Troppe pagine ricevute per {spec.label}; download interrotto.")
            params: dict[str, Any] = {
                "pageSize": 25 if spec.key in {"exercise", "sleep"} else 10000
            }
            filter_value = self._date_filter(spec, start, end)
            if filter_value:
                params["filter"] = filter_value
            if page_token:
                params["pageToken"] = page_token
            response = self._request(
                "GET",
                f"users/me/dataTypes/{spec.key}/dataPoints",
                params=params,
                cancel=cancel,
            )
            yield response.get("dataPoints", [])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
            if page_token in seen_tokens:
                raise ApiError(
                    508,
                    f"La Google Health API ha ripetuto una pagina per {spec.label}; "
                    "categoria ignorata.",
                )
            seen_tokens.add(page_token)

    def iter_daily_rollups(
        self,
        spec: DataTypeSpec,
        start: date,
        end: date,
        cancel: Callable[[], bool] | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        maximum_days = (
            14
            if spec.key
            in {
                "calories-in-heart-rate-zone",
                "heart-rate",
                "active-minutes",
                "total-calories",
            }
            else 90
        )
        cursor = start
        while cursor <= end:
            chunk_end = min(end + timedelta(days=1), cursor + timedelta(days=maximum_days))
            body = {
                "range": {
                    "startTime": datetime.combine(cursor, clock.min).astimezone().isoformat(),
                    "endTime": datetime.combine(chunk_end, clock.min).astimezone().isoformat(),
                },
                "windowSize": "86400s",
                "pageSize": 10000,
            }
            page_token = None
            seen_tokens: set[str] = set()
            while True:
                if page_token:
                    body["pageToken"] = page_token
                response = self._request(
                    "POST",
                    f"users/me/dataTypes/{spec.key}/dataPoints:rollUp",
                    json_body=body,
                    cancel=cancel,
                )
                yield response.get("rollupDataPoints", [])
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
                if page_token in seen_tokens:
                    raise ApiError(508, f"Pagina roll-up ripetuta per {spec.label}.")
                seen_tokens.add(page_token)
            cursor = chunk_end

    def get_resources(self, cancel: Callable[[], bool] | None = None) -> dict[str, dict]:
        resources = {}
        endpoints = {
            "identity": "users/me/identity",
            "profile": "users/me/profile",
            "settings": "users/me/settings",
            "paired-devices": "users/me/pairedDevices",
        }
        for name, path in endpoints.items():
            try:
                resources[name] = self._request("GET", path, cancel=cancel)
            except ApiError as exc:
                if exc.status not in {403, 404}:
                    raise
        return resources
