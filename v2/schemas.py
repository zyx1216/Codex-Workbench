# -*- coding: utf-8 -*-
"""
统一响应信封：所有 API 返回 {code, message, data}。
"""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResult(BaseModel, Generic[T]):
    """通用响应模型。code=0 成功，非 0 失败。"""

    code: int = 0
    message: str = "success"
    data: T | None = None

    @classmethod
    def ok(cls, data: T | None = None, message: str = "success") -> "ApiResult[T]":
        return cls(code=0, message=message, data=data)

    @classmethod
    def fail(cls, message: str, code: int = 1) -> "ApiResult[T]":
        return cls(code=code, message=message, data=None)
