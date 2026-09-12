"""Thin HTTP client for a ShadowScribe server.

Deliberately dependency-light (``httpx`` only) so ``pip install shadowscribe-client``
never drags a machine-learning stack onto a laptop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import ClientConfig


class ShadowScribeError(RuntimeError):
    pass


class ShadowScribeClient:
    def __init__(self, config: ClientConfig | None = None) -> None:
        self.cfg = config or ClientConfig.load()
        self._client = httpx.Client(
            base_url=self.cfg.endpoint.rstrip("/"),
            timeout=httpx.Timeout(self.cfg.timeout_s, connect=15.0),
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "shadowscribe-client"}
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"
        return headers

    # ------------------------------------------------------------------ infra
    def __enter__(self) -> ShadowScribeClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, **params) -> httpx.Response:
        clean = {k: v for k, v in params.items() if v is not None}
        try:
            resp = self._client.get(path, params=clean)
        except httpx.ConnectError as exc:
            raise ShadowScribeError(
                f"cannot reach {self.cfg.endpoint} — is the server running? ({exc})"
            ) from exc
        self._raise_for_status(resp)
        return resp

    def _post(self, path: str, **kwargs) -> httpx.Response:
        try:
            resp = self._client.post(path, **kwargs)
        except httpx.ConnectError as exc:
            raise ShadowScribeError(f"cannot reach {self.cfg.endpoint}: {exc}") from exc
        self._raise_for_status(resp)
        return resp

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise ShadowScribeError("401 Unauthorized — check SS_TOKEN (`ss login --token ...`)")
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise ShadowScribeError(f"HTTP {resp.status_code}: {detail}")

    # ------------------------------------------------------------------ reads
    def health(self) -> dict[str, Any]:
        return self._get("/v1/health").json()

    def ping(self) -> bool:
        try:
            r = self._client.get("/healthz")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def brief(
        self,
        *,
        hours: int | None = None,
        max_tokens: int | None = None,
        quotes: bool = True,
        entities: bool = True,
    ) -> str:
        resp = self._get(
            "/v1/context/brief",
            hours=hours or self.cfg.default_hours,
            max_tokens=max_tokens or self.cfg.default_max_tokens,
            quotes=quotes,
            entities=entities,
        )
        return resp.text

    def commitments(self, *, status: str = "open", limit: int = 50) -> dict[str, Any]:
        return self._get("/v1/commitments", status=status, limit=limit).json()

    def set_commitment(self, commitment_id: str, status: str) -> dict[str, Any]:
        resp = self._client.patch(f"/v1/commitments/{commitment_id}", params={"status": status})
        self._raise_for_status(resp)
        return resp.json()

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        return self._get("/v1/search", q=query, limit=limit).json()

    def timeline(self, day: str | None = None) -> dict[str, Any]:
        return self._get("/v1/timeline", day=day).json()

    def recordings(self, *, limit: int = 30, status: str | None = None) -> dict[str, Any]:
        return self._get("/v1/recordings", limit=limit, status=status).json()

    def recording(self, recording_id: str) -> dict[str, Any]:
        return self._get(f"/v1/recordings/{recording_id}").json()

    # ----------------------------------------------------------------- writes
    def upload(
        self,
        path: Path | str,
        *,
        session_hint: str | None = None,
        client_id: str = "desktop",
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            raise ShadowScribeError(f"{p} not found")
        with p.open("rb") as fh:
            files = {"file": (p.name, fh, "application/octet-stream")}
            data = {"client_id": client_id}
            if session_hint:
                data["session_hint"] = session_hint
            if recorded_at:
                data["recorded_at"] = recorded_at
            resp = self._post("/v1/ingest/audio", files=files, data=data)
        return resp.json()

    def reprocess(self, recording_id: str) -> dict[str, Any]:
        return self._post(f"/v1/recordings/{recording_id}/reprocess").json()

    def delete_recording(self, recording_id: str) -> dict[str, Any]:
        resp = self._client.delete(f"/v1/recordings/{recording_id}")
        self._raise_for_status(resp)
        return resp.json()

    def job(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/v1/jobs/{job_id}").json()

    # -------------------------------------------------------------- debugging
    def as_dict(self) -> dict[str, Any]:
        return json.loads(
            json.dumps({"endpoint": self.cfg.endpoint, "token_set": bool(self.cfg.token)})
        )
