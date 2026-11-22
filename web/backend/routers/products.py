"""标准产品接口（阶段 4）。

产品由预先计算并标准化后的文件构成，Web 只做：
查询 -> metcom_product.reader -> 展示 -> 下载。

路由与旧占位实现的区别：真实路径是四段式
``/{source}/{region}/{resolution}/{date}``，因此不存在 ``/{product_id}`` 通配路由
与后续段冲突的问题；所有业务逻辑在 ``services/product_service.py``。

演示数据（web/data/products/demo）与正式产品（web/data/products/standard）
在响应里以 ``is_demo`` 区分，前端必须对演示数据加醒目标注。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from models.schemas import (
    ProductDataPreview,
    ProductEntry,
    ProductListResponse,
    ProductSourcesResponse,
)
from product import paths
from services import product_service

router = APIRouter(prefix="/products", tags=["标准产品"])


def _not_found(source: str, region: str, resolution: str, date: str, hour: int | None) -> HTTPException:
    where = f"{source}/{region}/{resolution}/{date}"
    if hour is not None:
        where += f"/H{int(hour):02d}"
    return HTTPException(status_code=404, detail=f"未找到标准产品：{where}")


@router.get("", response_model=ProductListResponse, summary="标准产品目录")
def list_products(
    source: str | None = Query(None, description="按数据源过滤，如 ERA5 / era5"),
    region: str | None = Query(None, description="按区域过滤，如 BTH"),
    resolution: str | None = Query(None, description="按空间分辨率过滤，如 0p125"),
    date: str | None = Query(None, description="按日期过滤，如 20240101"),
    include_demo: bool = Query(True, description="是否包含演示数据"),
):
    """列出仓库里真实存在的产品记录，可按数据源 / 区域 / 分辨率 / 日期过滤。"""
    products = product_service.list_products(
        source=source, date=date, region=region, resolution=resolution,
        include_demo=include_demo,
    )
    return ProductListResponse(
        products=products,
        total=len(products),
        demo_included=include_demo,
    )


@router.get("/sources", response_model=ProductSourcesResponse, summary="标准产品数据源")
def list_sources(include_demo: bool = Query(True, description="是否包含演示数据")):
    """数据源及其实际可用的区域 / 分辨率 / 日期 / 时次（只列真实存在的数据）。"""
    return ProductSourcesResponse(
        sources=product_service.list_sources(include_demo=include_demo),
        demo_roots=[
            str(root)
            for root, demo in paths.list_roots(product_service.product_roots())
            if demo
        ],
        product_roots=[str(root) for root in product_service.product_roots()],
    )


@router.get(
    "/{source}/{region}/{resolution}/{date}",
    response_model=ProductEntry,
    summary="单个产品元信息",
)
def get_product(
    source: str,
    region: str,
    resolution: str,
    date: str,
    include_demo: bool = Query(True, description="是否包含演示数据"),
):
    """某数据源 / 区域 / 分辨率 / 日期下的全部时次与文件。"""
    detail = product_service.product_detail(
        source=source, region=region, resolution=resolution, date=date,
        include_demo=include_demo,
    )
    if detail is None:
        raise _not_found(source, region, resolution, date, None)
    return detail


@router.get(
    "/{source}/{region}/{resolution}/{date}/{hour}/data",
    response_model=ProductDataPreview,
    summary="产品数据预览",
)
def preview_product_data(
    source: str,
    region: str,
    resolution: str,
    date: str,
    hour: int,
    limit: int = Query(200, ge=0, description="返回行数上限，0 表示返回全部行"),
    include_demo: bool = Query(True, description="是否包含演示数据"),
):
    """读取某时次产品的 Header 与数据行（默认只返回前 200 行用于预览）。"""
    try:
        return product_service.preview_data(
            source=source, region=region, resolution=resolution, date=date,
            hour=hour, include_demo=include_demo,
            limit=None if limit == 0 else limit,
        )
    except FileNotFoundError as exc:
        raise _not_found(source, region, resolution, date, hour) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"产品文件无法解析：{exc}") from exc


@router.get(
    "/{source}/{region}/{resolution}/{date}/{hour}/download",
    summary="产品文件下载",
)
def download_product(
    source: str,
    region: str,
    resolution: str,
    date: str,
    hour: int,
    include_demo: bool = Query(True, description="是否包含演示数据"),
):
    """下载原始标准产品文件（文本流，Header + 数据区）。"""
    try:
        path = product_service.download_path(
            source=source, region=region, resolution=resolution, date=date,
            hour=hour, include_demo=include_demo,
        )
    except FileNotFoundError as exc:
        raise _not_found(source, region, resolution, date, hour) from exc

    if not path.is_file():
        raise _not_found(source, region, resolution, date, hour)
    return FileResponse(
        path=str(path),
        media_type="text/plain; charset=utf-8",
        filename=path.name,
    )


@router.get(
    "/{source}/{region}/{resolution}/{date}/archive",
    summary="产品日期级 ZIP 下载",
)
def download_product_archive(
    source: str,
    region: str,
    resolution: str,
    date: str,
    include_demo: bool = Query(True, description="是否包含演示数据"),
):
    """下载该产品日期的全部时次；ZIP 内保持仓库原有目录与文件名。"""
    try:
        archive = product_service.archive_path(
            source=source, region=region, resolution=resolution, date=date,
            include_demo=include_demo,
        )
    except FileNotFoundError as exc:
        raise _not_found(source, region, resolution, date, None) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return FileResponse(
        path=str(archive),
        media_type="application/zip",
        filename=product_service.archive_filename(source, region, resolution, date),
    )
