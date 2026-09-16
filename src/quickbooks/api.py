"""Minimal HTTP API around QuickBooksService. Read-only unless allow_writes."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from typing import Any

from .connection import FILE_MODE_DO_NOT_CARE, SerialQuickBooks
from .diagnose import run_diagnose
from .errors import QBError
from .models import to_dict
from .qbxml import is_additive_qbxml, is_read_only_qbxml
from .service import QuickBooksService

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import PlainTextResponse
except ImportError as exc:  # pragma: no cover
    raise ImportError("pip install fastapi uvicorn") from exc


def create_app(
    *,
    token: str | None = None,
    app_name: str = "qbctl",
    company_file: str = "",
    qbxml_version: str = "13.0",
    allow_writes: bool = False,
    allow_destructive: bool = False,
    file_mode: int = FILE_MODE_DO_NOT_CARE,
) -> FastAPI:
    runtime = SerialQuickBooks(
        app_name=app_name,
        company_file=company_file,
        file_mode=file_mode,
        read_only=not allow_writes,
        allow_destructive=allow_destructive,
    )
    mode = "read-write" if allow_destructive else ("add-only" if allow_writes else "read-only")
    expected_token = token or os.environ.get("QBCTL_TOKEN") or ""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(title="qbctl", version="0.1.0", lifespan=lifespan)

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not expected_token:
            return
        if authorization != f"Bearer {expected_token}":
            raise HTTPException(status_code=401, detail="Invalid or missing bearer token")

    def service() -> QuickBooksService:
        return _RuntimeService(runtime, qbxml_version)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": mode}

    @app.get("/diagnose")
    def diagnose(_: None = Depends(auth)) -> dict[str, Any]:
        return run_diagnose()

    @app.get("/host")
    def host(_: None = Depends(auth)) -> Any:
        return _call(lambda: to_dict(service().host()))

    @app.get("/company")
    def company(_: None = Depends(auth)) -> Any:
        return _call(lambda: to_dict(service().company()))

    @app.get("/accounts")
    def accounts(
        active_status: str = Query(default="All"),
        _: None = Depends(auth),
    ) -> Any:
        return _call(lambda: to_dict(service().accounts(active_status=active_status)))

    @app.get("/transactions")
    def transactions(
        from_date: str = Query(alias="from"),
        to_date: str = Query(alias="to"),
        _: None = Depends(auth),
    ) -> Any:
        return _call(lambda: to_dict(service().transactions(from_date=from_date, to_date=to_date)))

    @app.get("/reports/trial-balance")
    def trial_balance(
        date: str,
        basis: str | None = None,
        _: None = Depends(auth),
    ) -> Any:
        return _call(lambda: to_dict(service().trial_balance(as_of=date, report_basis=basis or None)))

    @app.get("/reports/profit-loss")
    def profit_loss(
        from_date: str = Query(alias="from"),
        to_date: str = Query(alias="to"),
        basis: str | None = None,
        _: None = Depends(auth),
    ) -> Any:
        return _call(
            lambda: to_dict(
                service().profit_loss(from_date=from_date, to_date=to_date, report_basis=basis or None)
            )
        )

    @app.post("/query")
    async def raw_query(request: Request, _: None = Depends(auth)) -> Any:
        document = (await request.body()).decode("utf-8")
        if not document.strip():
            raise HTTPException(status_code=400, detail="Empty request body; POST raw qbXML")
        if not allow_writes and not is_read_only_qbxml(document):
            raise HTTPException(
                status_code=400,
                detail="Read-only API: refusing add/mod/del/void qbXML (restart serve with --allow-writes)",
            )
        if allow_writes and not allow_destructive and not is_additive_qbxml(document):
            raise HTTPException(
                status_code=400,
                detail="Add-only API: refusing mod/del/void qbXML (restart serve with --allow-destructive)",
            )
        xml = await run_in_threadpool(lambda: _call(lambda: runtime.process(document)))
        return PlainTextResponse(xml, media_type="application/xml")

    def _call(fn):
        try:
            return fn()
        except QBError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # surface the reason instead of an opaque 500
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    app.state.token = expected_token
    return app


class _RuntimeService(QuickBooksService):
    def __init__(self, runtime: SerialQuickBooks, qbxml_version: str) -> None:
        self.runtime = runtime
        self.qbxml_version = qbxml_version
        self.connection = runtime  # type: ignore[assignment]

    def process(self, document: str) -> str:
        return self.runtime.process(document)


def run_server(
    *,
    host: str,
    port: int,
    token: str | None,
    app_name: str,
    company_file: str,
    qbxml_version: str,
    allow_writes: bool = False,
    allow_destructive: bool = False,
    file_mode: int = FILE_MODE_DO_NOT_CARE,
) -> None:
    import uvicorn

    pinned = bool(token or os.environ.get("QBCTL_TOKEN"))
    token = token or os.environ.get("QBCTL_TOKEN") or secrets.token_urlsafe(24)
    app = create_app(
        token=token,
        app_name=app_name,
        company_file=company_file,
        qbxml_version=qbxml_version,
        allow_writes=allow_writes,
        allow_destructive=allow_destructive,
        file_mode=file_mode,
    )
    mode = "READ-WRITE" if allow_destructive else ("ADD-ONLY" if allow_writes else "read-only")
    print(f"qbctl API on http://{host}:{port} ({mode})")
    if pinned:
        print("token: from --token / QBCTL_TOKEN (not printed)")
    else:
        print(f"token: {token}")
        print(f"From a client:  qbctl company --url http://{host}:{port} --token {token}")
    uvicorn.run(app, host=host, port=port, log_level="info")
