"""
Standard API response envelope and common schemas.
All API endpoints MUST use these wrappers — never return raw data directly.
"""
from __future__ import annotations

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str


class Meta(BaseModel):
    total: Optional[int] = None
    page: Optional[int] = None
    page_size: Optional[int] = None
    query_took_ms: Optional[int] = None
    warning_rules: Optional[list[dict]] = None  # §11.5: channel disable warning


class APIResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    meta: Optional[Meta] = None
    message: Optional[str] = None
    error: Optional[ErrorDetail] = None

    @classmethod
    def ok(
        cls, data: T, meta: Optional[Meta] = None, message: Optional[str] = None
    ) -> "APIResponse[T]":
        return cls(success=True, data=data, meta=meta, message=message, error=None)

    @classmethod
    def fail(cls, code: str, message: str) -> "APIResponse[None]":
        return APIResponse[None](  # type: ignore[arg-type]
            success=False,
            data=None,
            meta=None,
            message=None,
            error=ErrorDetail(code=code, message=message),
        )



