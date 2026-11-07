"""统一业务异常，由 main.py 转成可读 JSON 错误响应。"""
from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "BAD_REQUEST"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class NotFound(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, 404, "NOT_FOUND")


class Conflict(AppError):
    def __init__(self, message: str):
        super().__init__(message, 409, "CONFLICT")


class ValidationError(AppError):
    def __init__(self, message: str):
        super().__init__(message, 422, "VALIDATION_ERROR")


class ServiceUnavailable(AppError):
    def __init__(self, message: str, code: str = "SERVICE_UNAVAILABLE"):
        super().__init__(message, 503, code)
