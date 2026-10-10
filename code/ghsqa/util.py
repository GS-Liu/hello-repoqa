"""通用工具：时间、JSON、哈希与体积格式化。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

__all__ = [
    "now_iso",
    "parse_iso",
    "days_since",
    "read_json",
    "write_json",
    "ensure_dir",
    "sha256_file",
    "sha256_bytes",
    "human_bytes",
    "clamp",
    "safe_div",
]


def now_iso() -> str:
    """返回 UTC 当前时间的 ISO8601 字符串（以 Z 结尾），用于快照采集时间戳。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """解析 GitHub 返回的 ISO8601 时间；无法解析时返回 None。"""
    if not ts:
        return None
    raw = str(ts).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def days_since(ts: Optional[str], reference: Optional[datetime] = None) -> Optional[float]:
    """返回某时间戳距参考时间（默认现在）的天数，用于活跃度判定。"""
    dt = parse_iso(ts)
    if dt is None:
        return None
    ref = reference or datetime.now(timezone.utc)
    return (ref - dt).total_seconds() / 86400.0


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str, payload: Any) -> str:
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    return path


def ensure_dir(path: Optional[str]) -> None:
    if path:
        os.makedirs(path, exist_ok=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    """分块计算文件 sha256，用于校验下载的归档快照未被篡改。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def human_bytes(num: Optional[float]) -> str:
    if num is None:
        return "n/a"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0 or unit == "TB":
            return ("%.1f %s" % (value, unit)) if unit != "B" else ("%d B" % int(value))
        value /= 1024.0
    return "%.1f TB" % value


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if not denominator:
        return default
    return numerator / denominator


def truncate(text: Optional[str], limit: int = 160) -> str:
    """把长文本裁剪为单行摘要，用于报告表格。"""
    if not text:
        return ""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def score_level(score: float, pass_score: float, review_score: float) -> str:
    if score >= pass_score:
        return "PASS"
    if score >= review_score:
        return "REVIEW"
    return "REJECT"


def ratio_str(value: float) -> str:
    return "%.1f%%" % (value * 100.0)
