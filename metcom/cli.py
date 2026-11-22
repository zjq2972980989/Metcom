"""METCOM 命令行入口。

子命令与原 01~05 模块对应（详细用法见 docs/用户使用说明书.md）：

  metcom gridgen      01 格点文件生成
  metcom download     02 再分析数据下载
  metcom retrieve     03 核心 ZTD/PWV 反演
  metcom timeinterp   04 时间维度插值
  metcom evaluate     05 精度评估
  metcom plot         05 可视化绘图

配置体系：
  - 每个子命令的参数集中在 ``configs/metcom_<模块名称>.yaml``，键名与原
    MATLAB App 界面字段一一对应（配置文件内有注释对照）；
  - 数据源类配置文件名即数据源（``metcom_cra40.yaml`` → cra40），
    ``retrieve`` 据此自动推断，无需 --source；
  - 优先级：命令行参数 > 配置文件 > 代码内置默认值（取自原 App 初始值）。

用法示例::

    metcom retrieve -c configs/metcom_era5.yaml          # 按 ERA5 配置反演
    metcom retrieve -c configs/metcom_era5.yaml --start 2024010100 --end 2024010123
    metcom evaluate -c configs/metcom_evaluation.yaml --mode bystation

不安装也可运行：``python -m metcom.cli <子命令> ...``（与 metcom 命令等价）。
"""

from __future__ import annotations

from pathlib import Path

import click

from metcom import __version__
from metcom.config import apply_overrides, load_config


@click.group()
@click.version_option(version=__version__, prog_name="metcom")
def cli() -> None:
    """METCOM Python 版 —— 多源再分析资料反演 ZTD/PWV。"""


# ---------------------------------------------------------------- retrieve --
def _infer_source(config_path, explicit) -> str:
    """数据源推断：--source 显式指定 > 配置文件名（metcom_<源>.yaml）> 配置内 source 项。"""
    if explicit:
        return explicit
    if config_path is not None:
        stem = Path(config_path).stem          # metcom_cra40 -> cra40
        if stem.startswith("metcom_"):
            candidate = stem[len("metcom_"):]
            from metcom.sources import SOURCES
            if candidate in SOURCES:
                return candidate
    return "era5"


@cli.command()
@click.option("--source", "-s", type=click.Choice(["era5", "cmameso", "cra40", "crav15", "merra2"]),
              default=None, help="数据源（默认从配置文件名 metcom_<源>.yaml 推断）")
@click.option("--config", "-c", "config_path", type=click.Path(),
              default=None, help="配置文件路径（默认 configs/metcom_<source>.yaml）")
@click.option("--data-path", type=click.Path(), default=None, help="覆盖: 数据根目录")
@click.option("--output-path", type=click.Path(), default=None, help="覆盖: 输出目录")
@click.option("--output-format", type=click.Choice(["txt", "mat", "dat"]), default=None,
              help="覆盖: 输出格式")
@click.option("--site-file", type=click.Path(), default=None, help="覆盖: 站点列表文件")
@click.option("--start", "start_time", default=None, help="覆盖: 起始时间 YYYYMMDD[HH]")
@click.option("--end", "end_time", default=None, help="覆盖: 结束时间 YYYYMMDD[HH]")
@click.option("--parallel/--serial", default=None, help="覆盖: 是否站点级并行")
@click.option("--n-jobs", type=int, default=None, help="覆盖: 并行进程数")
def retrieve(source, config_path, data_path, output_path, output_format,
             site_file, start_time, end_time, parallel, n_jobs):
    """03 核心 ZTD/PWV 反演（对应原 METCOM_*.mlapp GUI）。"""
    from metcom.retrieval.pipeline import RetrievalOptions, run_retrieval
    from metcom.sources import SOURCES

    source = _infer_source(config_path, source)
    cfg = load_config(config_path or Path(f"configs/metcom_{source}.yaml"), source)
    cfg.setdefault("source", source)
    cfg = apply_overrides(cfg, {
        "data_path": data_path,
        "output_path": output_path,
        "output_format": output_format,
        "site_file": site_file,
        "start_time": start_time,
        "end_time": end_time,
        "parallel": parallel,
        "n_jobs": n_jobs,
    })

    spec = SOURCES[source]
    sr = cfg.get("spatial_resolution") or list(spec.spatial_resolution)
    options = RetrievalOptions(
        source_key=source,
        data_path=cfg["data_path"],
        output_path=cfg["output_path"],
        output_format=cfg.get("output_format", "txt"),
        site_file=cfg["site_file"],
        start_time=str(cfg["start_time"]),
        end_time=str(cfg["end_time"]),
        spatial_resolution=(float(sr[0]), float(sr[1])),
        time_resolution=cfg.get("time_resolution", spec.time_resolution),
        parallel=bool(cfg.get("parallel", False)),
        n_jobs=cfg.get("n_jobs"),
        egm_file=cfg.get("egm_file"),
        grib2=cfg.get("grib2") or {},
    )
    stats = run_retrieval(options)
    click.echo(f"完成: {stats}")


# ------------------------------------------------------------------- stubs --
@cli.command()
@click.option("--config", "-c", "config_path", type=click.Path(), default=None)
def gridgen(config_path):
    """01 格点文件生成（对应 01_MakeGrdText）。"""
    from metcom.gridgen import run_gridgen
    cfg = load_config(config_path, "gridgen")
    run_gridgen(cfg)


@cli.command()
@click.option("--config", "-c", "config_path", type=click.Path(), default=None)
def download(config_path):
    """02 再分析数据下载（对应 02_Downloader）。"""
    from metcom.downloader.runner import run_download
    cfg = load_config(config_path, "downloader")
    run_download(cfg)


@cli.command()
@click.option("--config", "-c", "config_path", type=click.Path(), default=None)
def timeinterp(config_path):
    """04 时间维度插值（对应 04_TimeInter）。"""
    from metcom.timeinterp.runner import run_timeinterp
    cfg = load_config(config_path, "timeinterp")
    run_timeinterp(cfg)


@cli.command()
@click.option("--config", "-c", "config_path", type=click.Path(), default=None)
@click.option("--mode", type=click.Choice(["timeseries", "bystation"]), default=None,
              help="覆盖: 评估模式")
def evaluate(config_path, mode):
    """05 精度评估（对应 compareByTime_Grid / compareByGrid）。"""
    from metcom.evaluation.runner import run_evaluation
    cfg = load_config(config_path, "evaluation")
    cfg = apply_overrides(cfg, {"eval_mode": mode})
    run_evaluation(cfg)


@cli.command()
@click.option("--config", "-c", "config_path", type=click.Path(), default=None)
def plot(config_path):
    """05 可视化绘图（对应 plot_ZTD_PWV_* 脚本）。"""
    from metcom.evaluation.runner import run_plot
    cfg = load_config(config_path, "plot")
    run_plot(cfg)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
