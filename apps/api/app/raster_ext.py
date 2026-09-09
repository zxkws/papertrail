"""位图编辑能力的可选装载。

onnxruntime + opencv 体积可观，只做矢量 PDF 的部署可以不装（`pip install .`），
需要处理扫描件时再装（`pip install '.[raster]'`）。未安装时相关接口返回 503，
而不是整个服务起不来。
"""
from __future__ import annotations

try:
    from . import raster

    IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - 取决于部署时装没装 extra
    raster = None
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

AVAILABLE = raster is not None
HINT = "位图页编辑需要可选依赖：pip install '.[raster]'"


def require():
    """需要位图能力的地方先调这个。"""
    if not AVAILABLE:
        raise RuntimeError(f"RASTER_UNAVAILABLE: {HINT}（{IMPORT_ERROR}）")
    return raster
