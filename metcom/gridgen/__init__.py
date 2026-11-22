"""01 格点文件生成模块（对应 01_MakeGrdText）。

shp_dem_to_region.py  <- ShpDemToRegion_Masked_region.m
orth_to_ell.py        <- OrthHeight2EllHeight.m
"""

from metcom.gridgen.orth_to_ell import run_orth_to_ell
from metcom.gridgen.shp_dem_to_region import run_dem_clip


def run_gridgen(cfg: dict) -> dict:
    """执行格点生成全流程（对应两个 .m 脚本顺序运行）。"""
    stats = {}
    if cfg.get("dem"):
        stats["dem"] = run_dem_clip(cfg["dem"])
    if cfg.get("stations"):
        stats["stations"] = run_orth_to_ell(cfg["stations"])
    return stats


__all__ = ["run_gridgen", "run_dem_clip", "run_orth_to_ell"]
