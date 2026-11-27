"""项目媒体预览的只读索引与文件校验；缓存不持有媒体字节或文件描述符。"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import BinaryIO

from ..models import Asset, StructuredDirectorDocument


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class ProjectAssetReader:
    def __init__(
        self, document_path: Path, read_document: Callable[[], StructuredDirectorDocument]
    ) -> None:
        self._path = document_path
        self._read = read_document
        self._lock = Lock()
        self._document_identity: tuple[int, ...] | None = None
        self._assets: dict[str, Asset] = {}
        self._verified: dict[str, tuple[str, tuple[int, ...]]] = {}

    def _asset(self, asset_id: str) -> Asset:
        with self._lock:
            info = self._path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise FileNotFoundError("结构化导演台尚未建立。")
            identity = _identity(info)
            if identity != self._document_identity:
                document = self._read()
                self._assets = {item.id: item for item in document.assets}
                self._verified = {k: v for k, v in self._verified.items() if k in self._assets}
                self._document_identity = identity
            asset = self._assets.get(asset_id)
            if asset is None:
                raise FileNotFoundError("Asset 不存在。")
            return asset

    def open(self, asset_id: str) -> tuple[Asset, BinaryIO]:
        asset = self._asset(asset_id)
        path = self._path.parent / asset.relative_path
        if path.is_symlink() or not path.is_file() or path.resolve() != path:
            raise ValueError("Asset 文件不存在或类型不合法。")
        # 成功时把文件所有权交给 HTTP 写线程，Range 校验与传输共用这一描述符。
        stream = open(path, "rb", opener=_open_regular)  # noqa: SIM115
        try:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != asset.byte_count:
                raise ValueError("Asset 内容校验失败。")
            version = (asset.sha256, _identity(info))
            with self._lock:
                verified = self._verified.get(asset.id) == version
            if not verified:
                if hashlib.file_digest(stream, "sha256").hexdigest() != asset.sha256:
                    raise ValueError("Asset 内容校验失败。")
                stream.seek(0)
                with self._lock:
                    self._verified[asset.id] = version
            return asset, stream
        except BaseException:
            stream.close()
            raise


def _open_regular(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK)
