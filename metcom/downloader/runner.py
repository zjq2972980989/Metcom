"""ERA5 再分析数据下载（02_Downloader 的迁移整合版）。

架构与原脚本一致：主进程串行请求 CDS 链接 → 任务队列 → 多工作进程并行下载
（requests 流式或 IDM 命令行），支持断点续传与重试。

相对原脚本的改动（见 docs/porting_notes.md）：
  - 全部参数由 configs/metcom_downloader.yaml 提供（替代脚本顶部"用户配置区"）；
  - 修复两处裸 except（改为捕获具体异常）；
  - 完整文件判定阈值 MIN_FILE_SIZE 参数化（原硬编码 220MB）；
  - .cdsapirc 相对路径解析顺序：工作目录 → 配置文件所在目录 → 用户主目录
    （~/.cdsapirc）→ 原软件包目录。
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import subprocess
import time
from datetime import datetime, timedelta
from multiprocessing import Manager, Process
from pathlib import Path

PRESSURE_LEVELS = [
    '1', '2', '3', '5', '7', '10',
    '20', '30', '50', '70', '100', '125',
    '150', '175', '200', '225', '250', '300',
    '350', '400', '450', '500', '550', '600',
    '650', '700', '750', '775', '800', '825',
    '850', '875', '900', '925', '950', '975',
    '1000',
]
GRID = '0.25/0.25'
VARIABLES = ['geopotential', 'relative_humidity', 'specific_humidity', 'temperature']
DATASET = 'reanalysis-era5-pressure-levels'
RETRY_BACKOFF = 30
IDM_POLL_INTERVAL = 5
IDM_TIMEOUT = 1200


# ---------------------------------------------------------------- 工具函数 --
def parse_date(s: str) -> datetime:
    if len(s) != 10 or not s.isdigit():
        raise ValueError(f"日期格式错误: '{s}'，应为 YYYYMMDDHH")
    return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]))


def generate_time_list(start_dt: datetime, end_dt: datetime) -> list[datetime]:
    times, cur = [], start_dt
    while cur <= end_dt:
        times.append(cur)
        cur += timedelta(hours=1)
    return times


def parse_cdsapirc(path: Path) -> dict:
    cfg = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                k, _, v = line.partition(':')
                k, v = k.strip(), v.strip()
                if k == 'url':
                    cfg['url'] = v
                elif k == 'key':
                    cfg['key'] = v
    if 'url' not in cfg or 'key' not in cfg:
        raise ValueError(f"配置文件 {path} 中未找到 url 或 key")
    return cfg


def get_filepath(dt: datetime, output_dir, fmt: str) -> str:
    ext = '.grib' if fmt == 'grib' else '.nc'
    return os.path.join(output_dir, dt.strftime('%Y%m%d'),
                        f"era5_global_qrzt_{dt:%Y%m%d%H}{ext}")


def setup_logging(output_dir=None, add_file=True) -> logging.Logger:
    logger = logging.getLogger('ERA5')
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s  %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    if add_file and output_dir:
        fh = logging.FileHandler(os.path.join(output_dir, 'download_era5.log'),
                                 mode='a', encoding='utf-8')
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


def resolve_cdsapirc(path: str, config_dir: Path | None) -> Path:
    """解析 .cdsapirc 路径。

    绝对路径存在时直接使用；相对路径依次尝试工作目录、配置文件所在目录、
    用户主目录（~/.cdsapirc），最后回退到原软件包目录兼容逻辑。
    """
    p = Path(path).expanduser()
    if p.is_absolute() and p.is_file():
        return p
    candidates = [Path.cwd() / p]
    if config_dir is not None:
        candidates.append(config_dir / p)
        candidates.append(config_dir.parent / p)
    candidates.append(Path.home() / ".cdsapirc")
    pkg_default = (Path(__file__).resolve().parents[3] / "METCOM_ver3.1"
                   / "02_Downloader" / "Downloader_ERA5")
    candidates.append(pkg_default / p)
    candidates.append(pkg_default / "CDSAPI" / Path(path).name)
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(f"找不到 .cdsapirc 配置文件: {path}")


# ---------------------------------------------------------------- 请求/下载 --
def request_url(client, dt: datetime, output_dir, fmt, logger, max_retries=3):
    filepath = get_filepath(dt, output_dir, fmt)
    filename = os.path.basename(filepath)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    cds_format = 'netcdf' if fmt == 'nc' else 'grib'
    params = {
        'product_type': 'reanalysis',
        'data_format': cds_format,
        'variable': VARIABLES,
        'pressure_level': PRESSURE_LEVELS,
        'year': dt.strftime('%Y'),
        'month': dt.strftime('%m'),
        'day': dt.strftime('%d'),
        'time': dt.strftime('%H') + ':00',
    }
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                logger.warning(f'[重试 {attempt - 1}/{max_retries}] {filename}')
            result = client.retrieve(DATASET, params)
            url = getattr(result, 'location', None) or getattr(result, 'url', None)
            if url:
                return url, filepath
            logger.error(f'[错误] {filename} 无法获取下载链接')
            return None
        except Exception as e:  # noqa: BLE001
            logger.error(f'[请求失败] {filename}: {e}')
            if attempt < max_retries:
                time.sleep(RETRY_BACKOFF * attempt)
    logger.error(f'[放弃] {filename} — 请求阶段全部失败')
    return None


def download_requests(url, filepath, max_dl_retries=5, min_file_size=220 * 1024 * 1024):
    from tqdm import tqdm
    import requests

    filename = os.path.basename(filepath)
    for attempt in range(1, max_dl_retries + 1):
        try:
            if os.path.isfile(filepath):
                os.remove(filepath)
        except OSError:
            pass
        try:
            with requests.get(url, stream=True, timeout=600) as resp:
                resp.raise_for_status()
                total_size = int(resp.headers.get('content-length', 0))
                with open(filepath, 'wb') as f:
                    with tqdm(total=total_size or None, unit='B', unit_scale=True,
                              desc=filename, leave=False) as pbar:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                pbar.update(len(chunk))
            if os.path.isfile(filepath) and os.path.getsize(filepath) >= min_file_size:
                return True
            if attempt < max_dl_retries:
                time.sleep(RETRY_BACKOFF * attempt)
                continue
        except Exception:  # noqa: BLE001
            if attempt < max_dl_retries:
                time.sleep(RETRY_BACKOFF * attempt)
                continue
    return False


def download_idm(url, filepath, idm_path, min_file_size=220 * 1024 * 1024):
    folder = os.path.dirname(filepath)
    os.makedirs(folder, exist_ok=True)
    filename = os.path.basename(filepath)
    cmd = [idm_path, '/d', url, '/p', folder, '/f', filename, '/n']
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:  # 修复原版裸 except
        logging.getLogger('ERA5').debug(f'IDM 提交失败: {e}')
        return False

    start = time.time()
    while time.time() - start < IDM_TIMEOUT:
        if os.path.isfile(filepath) and os.path.getsize(filepath) >= min_file_size:
            return True
        time.sleep(IDM_POLL_INTERVAL)
    return False


def worker(task_queue, result_queue, use_idm, idm_path, min_file_size,
           max_dl_retries):
    """工作进程主循环（原版 worker 修复版：队列空轮询用 queue.Empty 判断）。"""
    import queue as queue_mod

    logger = setup_logging(add_file=False)
    proc_name = mp.current_process().name
    while True:
        try:
            task = task_queue.get(timeout=1)
        except queue_mod.Empty:
            continue
        if task is None:
            break
        dt, url, filepath = task
        filename = os.path.basename(filepath)
        if use_idm:
            success = download_idm(url, filepath, idm_path, min_file_size)
        else:
            success = download_requests(url, filepath, max_dl_retries, min_file_size)
        if success:
            size_mb = os.path.getsize(filepath) / 1024 / 1024
            logger.info(f'[{proc_name}] 成功: {filename} ({size_mb:.1f} MB)')
        else:
            size_mb = 0.0
            logger.error(f'[{proc_name}] 失败: {filename}')
        result_queue.put((dt, filepath, success, size_mb))


# ---------------------------------------------------------------- 主流程 --
def run_download(cfg: dict, config_dir: Path | None = None) -> dict:
    """运行入口（配置节 metcom_downloader.yaml）。"""
    mp.set_start_method('spawn', force=True)

    fmt = str(cfg.get('format', 'nc')).strip().lower()
    if fmt in ('netcdf', 'nc'):
        fmt = 'nc'
    elif fmt in ('grib', 'grb'):
        fmt = 'grib'
    else:
        raise ValueError(f"不支持的格式 '{cfg.get('format')}'，可选 'grib' 或 'nc'")

    start_dt = parse_date(str(cfg['start_time']))
    end_dt = parse_date(str(cfg['end_time']))
    if start_dt > end_dt:
        raise ValueError("起始时间不能晚于结束时间")

    output_dir = Path(cfg.get('output_dir') or 'era5_data')
    output_dir = output_dir if output_dir.is_absolute() else Path.cwd() / output_dir
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(str(output_dir), add_file=True)

    use_idm = bool(cfg.get('use_idm', False))
    workers = int(cfg.get('download_workers', 4))
    min_file_size = int(float(cfg.get('min_file_size_mb', 220)) * 1024 * 1024)
    max_req = int(cfg.get('request_retries', 3))
    max_dl = int(cfg.get('download_retries', 5))

    idm_path = cfg.get('idm_path', '')
    if use_idm and not os.path.isfile(idm_path):
        raise FileNotFoundError(f'IDM 路径无效: {idm_path}')

    cdsapirc = resolve_cdsapirc(str(cfg['cdsapirc_path']), config_dir)
    rc = parse_cdsapirc(cdsapirc)
    logger.info(f'配置文件: {cdsapirc}')

    import cdsapi
    client = cdsapi.Client(url=rc['url'], key=rc['key'], quiet=True)

    time_list = generate_time_list(start_dt, end_dt)
    total = len(time_list)

    logger.info('=' * 60)
    logger.info(f'  时间范围    : {cfg["start_time"]} -> {cfg["end_time"]}（{total} 个时刻）')
    logger.info(f'  输出目录    : {output_dir}')
    logger.info(f'  数据格式    : {fmt.upper()}')
    logger.info(f'  使用 IDM    : {"是" if use_idm else "否"}')
    logger.info(f'  下载并发进程数: {workers}')
    logger.info('=' * 60)

    manager = Manager()
    task_queue = manager.Queue()
    result_queue = manager.Queue()
    processes = []
    for _ in range(workers):
        p = Process(target=worker, args=(task_queue, result_queue, use_idm,
                                         idm_path, min_file_size, max_dl))
        p.daemon = True
        p.start()
        processes.append(p)

    skipped = req_failed = dl_success = dl_failed = 0
    req_failed_times, dl_failed_times = [], []
    t0 = time.time()
    min_size_mb = min_file_size / (1024 * 1024)

    for i, dt in enumerate(time_list, 1):
        filepath = get_filepath(dt, str(output_dir), fmt)
        if os.path.isfile(filepath):
            file_size = os.path.getsize(filepath)
            if file_size >= min_file_size:
                logger.info(f'[{i}/{total}] {dt:%Y-%m-%d %H:00} → 跳过 '
                            f'(已存在, 大小: {file_size / 1048576:.1f} MB)')
                skipped += 1
                continue
            logger.warning(f'[{i}/{total}] {dt:%Y-%m-%d %H:00} → 发现不完整文件 '
                           f'(大小: {file_size / 1048576:.1f} MB < {min_size_mb:.1f} MB)，'
                           f'将删除并重新下载')
            try:
                os.remove(filepath)
            except OSError:
                pass

        result = request_url(client, dt, str(output_dir), fmt, logger, max_req)
        if not result:
            logger.error(f'[{i}/{total}] {dt:%Y-%m-%d %H:00} → 请求失败')
            req_failed += 1
            req_failed_times.append(dt)
            continue
        url, fp = result
        task_queue.put((dt, url, fp))
        logger.info(f'[{i}/{total}] {dt:%Y-%m-%d %H:00} → 入队')

    for _ in range(workers):
        task_queue.put(None)
    for p in processes:
        p.join()

    while not result_queue.empty():
        dt, filepath, success, size_mb = result_queue.get()
        if success:
            dl_success += 1
            logger.info(f'[下载成功] {dt:%Y-%m-%d %H:00} | {os.path.basename(filepath)} '
                        f'({size_mb:.1f} MB)')
        else:
            dl_failed += 1
            dl_failed_times.append(dt)
            logger.error(f'[下载失败] {dt:%Y-%m-%d %H:00} | {os.path.basename(filepath)}')

    total_success = skipped + dl_success
    total_failed = req_failed + dl_failed
    elapsed = (time.time() - t0) / 60
    logger.info(f'{"=" * 60}')
    logger.info(f'  跳过（已存在）  : {skipped}')
    logger.info(f'  请求失败        : {req_failed}')
    for dt in req_failed_times:
        logger.info(f'     - {dt:%Y-%m-%d %H:00}')
    logger.info(f'  下载成功        : {dl_success}')
    logger.info(f'  下载失败        : {dl_failed}')
    for dt in dl_failed_times:
        logger.info(f'     - {dt:%Y-%m-%d %H:00}')
    logger.info(f'  总计            : {total} 个时刻，'
                f'有效成功 {total_success}，失败 {total_failed}，耗时 {elapsed:.1f} 分钟')
    logger.info('=' * 60)

    return {"total": total, "skipped": skipped, "success": dl_success + skipped,
            "failed": total_failed}
