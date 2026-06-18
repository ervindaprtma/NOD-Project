"""
Standard API response envelope and common schemas.
All API endpoints MUST use these wrappers — never return raw data directly.
"""
from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar

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


class APIResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    meta: Optional[Meta] = None
    error: Optional[ErrorDetail] = None

    @classmethod
    def ok(cls, data: T, meta: Optional[Meta] = None) -> "APIResponse[T]":
        return cls(success=True, data=data, meta=meta, error=None)

    @classmethod
    def fail(cls, code: str, message: str) -> "APIResponse[None]":
        return cls(
            success=False,
            data=None,
            meta=None,
            error=ErrorDetail(code=code, message=message),
        )


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = 25
    sort_by: Optional[str] = None
    sort_dir: Optional[str] = "desc"  # asc | desc
