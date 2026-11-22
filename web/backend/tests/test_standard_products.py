"""标准产品模块（阶段 4）测试。

覆盖：数据源清单只列真实存在的数据、演示数据标注、跨平台索引路径解析、
详情 / 预览 / 下载链路，以及"仓库为空"与"产品不存在"两种边界。

所有产品仓库都建在 tmp_path 下，不触碰真实 web/data。
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from metcom_product.converter import convert_one_txt
from metcom_product.rebuild_index import rebuild_index


def _product_text(epoch: str = "2024-01-01 00:00 UTC", rows: int = 3) -> str:
    """按交付格式造一份最小标准产品（14 行 Header + 数据区）。"""
    header = [
        "! MetCom Standard Tropospheric Product",
        "! Version: 1.0",
        "! Product_Type: STANDARD",
        "! Source: ERA5",
        "! Region: BTH",
        "! Resolution: 0.125 x 0.125 degree",
        f"! Epoch: {epoch}",
        "! Time_System: UTC",
        "! Longitude_Range: 113 120",
        "! Latitude_Range: 36 43",
        "! Parameters: PWV WVD Tm ZTD ZHD ZWD",
        "! Units: PWV[m] WVD[g/m^3] Tm[K] ZTD[m] ZHD[m] ZWD[m]",
        "!",
        "! latitude longitude PWV WVD Tm ZTD ZHD ZWD",
    ]
    data = [
        f"{36.0 + index / 8:.3f} 115.000 0.009698 4.287387 266.512461 2.415469 2.350027 0.065442"
        for index in range(rows)
    ]
    return "\n".join(header + data) + "\n"


def _build_root(root: Path, rel: str, content: str, index_rel_style: str = "posix") -> None:
    """写入一个产品文件并更新索引。

    与真实索引一致：按 (source, region, resolution, date) 归并，同一天的多个时次
    合并到同一条记录里；`index_rel_style="windows"` 时用反斜杠写路径，
    验证跨平台兼容。
    """
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    # 文件名形如 MetCom_{source}_{region}_{resolution}_{date}.H{hh}
    stem, _, hour_text = target.name.partition(".H")
    _, source, region, resolution, date = stem.split("_")
    hour = int(hour_text)

    stored = rel.replace("/", "\\") if index_rel_style == "windows" else rel

    index_file = root / "index.json"
    index = {"version": "1.0", "updated": "2026-01-01T00:00:00+00:00", "products": []}
    if index_file.is_file():
        index = json.loads(index_file.read_text(encoding="utf-8"))

    key = (source, region, resolution, date)
    for item in index["products"]:
        if (item["source"], item["region"], item["resolution"], item["date"]) == key:
            if hour not in item["hours"]:
                item["hours"].append(hour)
                item["hours"].sort()
            if stored not in item["files"]:
                item["files"].append(stored)
                item["files"].sort()
            break
    else:
        index["products"].append({
            "source": source,
            "region": region,
            "resolution": resolution,
            "date": date,
            "hours": [hour],
            "files": [stored],
        })

    index_file.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def product_roots(tmp_path, monkeypatch):
    """正式仓库为空 + 演示仓库一个产品，返回两个根目录。"""
    data_dir = tmp_path / "web-data"
    demo = data_dir / "products" / "demo"
    standard = data_dir / "products" / "standard"
    standard.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("METCOM_WEB_DATA_DIR", str(data_dir))

    import config
    config.get_settings.cache_clear()
    yield standard, demo
    config.get_settings.cache_clear()


@pytest.fixture()
def client():
    return TestClient(app)


def test_sources_are_empty_without_products(product_roots, client):
    """两个仓库都没有产品时：清单为空，且不报错、不造数据。"""
    standard, demo = product_roots
    assert standard.is_dir()
    assert not demo.exists()

    response = client.get("/api/products/sources")
    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"] == []

    catalog = client.get("/api/products").json()
    assert catalog["total"] == 0
    assert catalog["products"] == []


def test_sources_only_list_existing_data(product_roots, client):
    """清单只包含磁盘上真实存在的数据源 / 区域 / 分辨率 / 日期 / 时次。"""
    _standard, demo = product_roots
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00",
        _product_text(),
    )

    payload = client.get("/api/products/sources").json()
    assert len(payload["sources"]) == 1
    source = payload["sources"][0]
    assert source["source"] == "ERA5"
    assert source["dir_name"] == "output_ERA5"
    assert source["regions"] == ["BTH"]
    assert source["resolutions"] == ["0p125"]
    assert source["dates"] == ["20240101"]
    assert source["hours"] == [0]
    assert source["product_count"] == 1
    assert source["is_demo"] is True

    # 没有数据的数据源（MERRA2 / CRA40 / ...）不允许出现
    names = {item["source"] for item in payload["sources"]}
    assert "MERRA2" not in names


def test_catalog_marks_demo_and_accepts_web_source_key(product_roots, client):
    """演示数据必须带 is_demo；Web 侧小写数据源 key 也能查询。"""
    _standard, demo = product_roots
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00",
        _product_text(),
    )

    payload = client.get("/api/products?source=era5&date=20240101").json()
    assert payload["total"] == 1
    record = payload["products"][0]
    assert record["is_demo"] is True
    assert record["file_count"] == 1
    assert record["hours"] == [0]
    assert record["files"][0]["hour"] == 0
    assert record["files"][0]["size_bytes"] > 0

    excluded = client.get("/api/products?include_demo=false").json()
    assert excluded["total"] == 0


def test_windows_style_index_paths_resolve(product_roots, client):
    """索引里带反斜杠的历史路径必须能解析（跨平台兼容）。"""
    _standard, demo = product_roots
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00",
        _product_text(),
        index_rel_style="windows",
    )

    index_text = (demo / "index.json").read_text(encoding="utf-8")
    assert "\\\\" in index_text  # 索引里确实是 Windows 反斜杠

    detail = client.get("/api/products/ERA5/BTH/0p125/20240101")
    assert detail.status_code == 200
    assert detail.json()["files"][0]["path"].count("/") > 0


def test_index_entries_without_files_are_skipped(product_roots, client):
    """索引里有记录、磁盘上没有文件时，该记录不得出现在响应里。"""
    _standard, demo = product_roots
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "index.json").write_text(
        json.dumps({
            "version": "1.0",
            "updated": None,
            "products": [{
                "source": "ERA5",
                "region": "China",
                "resolution": "1x1",
                "date": "20250101",
                "hours": [0],
                "files": ["ERA5/China/1x1/2025/2025001/MetCom_ERA5_China_1x1_20250101.H00"],
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = client.get("/api/products").json()
    assert payload["total"] == 0
    assert client.get("/api/products/sources").json()["sources"] == []


def test_detail_preview_and_download(product_roots, client):
    """详情 → 预览（Header + 数据）→ 下载 完整链路。"""
    _standard, demo = product_roots
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00",
        _product_text(rows=5),
    )

    detail = client.get("/api/products/ERA5/BTH/0p125/20240101")
    assert detail.status_code == 200
    assert detail.json()["hours"] == [0]
    assert detail.json()["is_demo"] is True

    preview = client.get("/api/products/ERA5/BTH/0p125/20240101/0/data?limit=2")
    assert preview.status_code == 200
    body = preview.json()
    assert body["filename"] == "MetCom_ERA5_BTH_0p125_20240101.H00"
    assert body["is_demo"] is True
    assert body["total_rows"] == 5
    assert body["returned_rows"] == 2
    assert body["columns"] == ["latitude", "longitude", "PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"]
    assert body["metadata"]["Source"] == "ERA5"
    assert body["units"]["ZTD"] == "m"

    full = client.get("/api/products/ERA5/BTH/0p125/20240101/0/data?limit=0")
    assert full.json()["returned_rows"] == 5

    download = client.get("/api/products/ERA5/BTH/0p125/20240101/0/download")
    assert download.status_code == 200
    assert "MetCom_ERA5_BTH_0p125_20240101.H00" in download.headers["content-disposition"]
    assert download.text.startswith("! MetCom Standard Tropospheric Product")


def test_zero_hour_is_a_valid_selection(product_roots, client):
    """H00 是有效时次：详情 / 预览 / 下载都必须可用（不能把 0 当空值）。"""
    _standard, demo = product_roots
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00",
        _product_text(),
    )

    detail = client.get("/api/products/ERA5/BTH/0p125/20240101")
    assert detail.status_code == 200
    assert detail.json()["hours"] == [0]

    preview = client.get("/api/products/ERA5/BTH/0p125/20240101/0/data")
    assert preview.status_code == 200
    assert preview.json()["hour"] == 0
    assert preview.json()["filename"].endswith(".H00")

    download = client.get("/api/products/ERA5/BTH/0p125/20240101/0/download")
    assert download.status_code == 200
    assert download.text.splitlines()[0] == "! MetCom Standard Tropospheric Product"


def test_catalog_without_filters_returns_every_record(product_roots, client):
    """不带任何过滤条件时返回全部记录（前端首屏加载走的就是这条路）。"""
    _standard, demo = product_roots
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00",
        _product_text(),
    )
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024002/MetCom_ERA5_BTH_0p125_20240102.H00",
        _product_text(epoch="2024-01-02 00:00 UTC"),
    )

    payload = client.get("/api/products").json()
    assert payload["total"] == 2
    assert sorted(record["date"] for record in payload["products"]) == ["20240101", "20240102"]


def test_archive_downloads_every_hour_and_preserves_repository_layout(product_roots, client):
    """日期级 ZIP 包含全部时次；文件名与仓库相对目录保持不变，内容为 UTF-8 文本。"""
    _standard, demo = product_roots
    rel_h00 = "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00"
    rel_h06 = "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H06"
    _build_root(demo, rel_h00, _product_text(rows=2))
    _build_root(
        demo,
        rel_h06,
        _product_text(epoch="2024-01-01 06:00 UTC", rows=2),
    )

    response = client.get("/api/products/ERA5/BTH/0p125/20240101/archive")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "MetCom_ERA5_BTH_0p125_20240101.zip" in response.headers["content-disposition"]

    with zipfile.ZipFile(BytesIO(response.content)) as bundle:
        assert bundle.testzip() is None
        assert bundle.namelist() == [rel_h00, rel_h06]
        for rel in bundle.namelist():
            content = bundle.read(rel).decode("utf-8")
            assert content.splitlines()[0] == "! MetCom Standard Tropospheric Product"
            assert "\x00" not in content


def test_conversion_header_uses_requested_resolution_and_rejects_bad_rows(tmp_path):
    """Header 必须反映实际分辨率；原始 TXT 的畸形行不能被静默丢弃。"""
    raw = tmp_path / "ztd_2024010100_sit_era5.txt"
    raw.write_text(
        "latitude longitude ZTD(m) ZHD(m) ZWD(m) PWV(m) Tm(K) WVD\n"
        "36.000 115.000 2.4 2.3 0.1 0.01 266.5 4.2\n"
        "36.000\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="列数"):
        convert_one_txt(
            raw, source="ERA5", resolution="1x1",
            output_root=tmp_path, update_idx=False,
        )

    raw.write_text(
        "latitude longitude ZTD(m) ZHD(m) ZWD(m) PWV(m) Tm(K) WVD\n"
        "36.000 115.000 2.4 2.3 0.1 0.01 266.5 4.2\n",
        encoding="utf-8",
    )
    out = convert_one_txt(
        raw, source="ERA5", resolution="1x1",
        output_root=tmp_path, update_idx=False,
    )
    text = out.read_text(encoding="utf-8")
    assert "! Resolution: 1 x 1 degree" in text


def test_rebuild_index_skips_unparseable_product_names(tmp_path):
    """索引重建不能因一个非法文件名而丢弃其他合法产品。"""
    valid_dir = tmp_path / "ERA5" / "BTH" / "0p125" / "2024" / "2024001"
    valid_dir.mkdir(parents=True)
    (valid_dir / "MetCom_ERA5_BTH_0p125_20240101.H00").write_text(
        _product_text(), encoding="utf-8",
    )
    (valid_dir / "MetCom_bad.H00").write_text("bad", encoding="utf-8")

    index = rebuild_index(tmp_path)

    assert len(index["products"]) == 1
    assert index["products"][0]["files"][0].replace("\\", "/") == (
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00"
    )


def test_index_record_with_empty_hours_is_skipped(product_roots, client):
    """索引里 hours 为空的记录不参与查询，也不会让请求报 500。"""
    _standard, demo = product_roots
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00",
        _product_text(),
    )
    index_file = demo / "index.json"
    index = json.loads(index_file.read_text(encoding="utf-8"))
    index["products"].append({
        "source": "ERA5",
        "region": "BTH",
        "resolution": "0p125",
        "date": "20240103",
        "hours": [],
        "files": [],
    })
    index_file.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    assert client.get("/api/products?date=20240103").json()["total"] == 0
    assert client.get("/api/products/ERA5/BTH/0p125/20240103").status_code == 404
    assert client.get("/api/products?date=20240101").json()["total"] == 1


@pytest.mark.parametrize("path", [
    "/api/products/ERA5/BTH/0p125/20240101",
    "/api/products/ERA5/BTH/0p125/20240101/0/data",
    "/api/products/ERA5/BTH/0p125/20240101/0/download",
    "/api/products/ERA5/BTH/0p125/20240101/archive",
])
def test_products_are_404_when_catalog_is_empty(client, path, product_roots):
    """仓库为空时详情 / 预览 / 单文件 / ZIP 入口都是 404，而不是 500 / 501。"""
    _standard, demo = product_roots
    assert not demo.exists()

    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", [
    "/api/products/ERA5/BTH/0p125/19990101",
    "/api/products/ERA5/BTH/0p125/20240101/6/data",
    "/api/products/ERA5/BTH/0p125/20240101/6/download",
    "/api/products/ERA5/BTH/0p125/19990101/archive",
    "/api/products/MERRA2/BTH/0p125/20240101",
])
def test_missing_products_return_404(product_roots, client, path):
    """不存在的日期 / 时次 / 数据源返回 404（不是 501、不是 500）。"""
    _standard, demo = product_roots
    _build_root(
        demo,
        "ERA5/BTH/0p125/2024/2024001/MetCom_ERA5_BTH_0p125_20240101.H00",
        _product_text(),
    )

    response = client.get(path)
    assert response.status_code == 404
    assert "未找到标准产品" in response.json()["detail"]
