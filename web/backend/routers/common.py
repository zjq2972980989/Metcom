"""路由层公共工具。"""

from __future__ import annotations

from fastapi import HTTPException


def not_implemented(planned_phase: str, feature: str) -> HTTPException:
    """阶段一占位：接口已注册、契约已固定，实现放到后续阶段。"""
    return HTTPException(
        status_code=501,
        detail=f"{feature}：接口契约已固定，将在 {planned_phase} 实现。",
    )