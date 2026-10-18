"""Bounded binary upload reception, independent of table decoding/validation.

The caller owns a successfully returned temporary file and must either adopt it
as an immutable input or remove it if subsequent validation fails. No filename
supplied by the browser is used, and reception never decodes or rewrites bytes.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import BinaryIO

from gui.table_import import InputError

MIB = 1024 * 1024
DEFAULT_UPLOAD_MIB = 100
MAX_UPLOAD_MIB = 200
DEFAULT_UPLOAD_BYTES = DEFAULT_UPLOAD_MIB * MIB
MAX_UPLOAD_BYTES = MAX_UPLOAD_MIB * MIB
CHUNK_BYTES = MIB
MAX_JSON_BYTES = MIB


def _positive_integer(value, message):
    # bool is an int subclass; int(float) would silently truncate a fraction.
    if isinstance(value, bool):
        raise InputError(message)
    if isinstance(value, str):
        value = value.strip()
        if not value or not value.isascii() or not value.isdecimal():
            raise InputError(message)
        try:
            value = int(value)
        except ValueError:
            raise InputError(message) from None
    if not isinstance(value, int) or value <= 0:
        raise InputError(message)
    return value


def choose_upload_limit(megabytes=DEFAULT_UPLOAD_MIB) -> int:
    """Return a 1–200 MiB byte limit from an integer or decimal CLI string."""
    message = '上传大小上限必须是 1–200 MiB 的整数'
    value = _positive_integer(megabytes, message)
    if value > MAX_UPLOAD_MIB:
        raise InputError(message)
    return value * MIB


def receive_upload(stream: BinaryIO, length, directory, max_bytes=DEFAULT_UPLOAD_BYTES) -> Path:
    """Save exactly ``length`` bytes using reads of at most one MiB.

    ``length`` may be an integer or an HTTP Content-Length decimal string.
    Missing, empty, malformed and excessive lengths are rejected before reading
    or creating files. A short read is allowed; early EOF is an error. Any error
    after file creation removes the partial upload. The HTTP caller must reject
    ambiguous framing (duplicate lengths or Transfer-Encoding) itself.
    """
    limit = _positive_integer(max_bytes, '上传大小上限无效')
    size = _positive_integer(length, '上传文件为空或 Content-Length 无效')
    if size > limit:
        raise InputError(f'文件超过当前上传上限 {limit / MIB:g} MiB')

    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='upload-', suffix='.tmp',
                                         dir=destination, delete=False) as handle:
            path = Path(handle.name)
            remaining = size
            while remaining:
                requested = min(remaining, CHUNK_BYTES)
                chunk = stream.read(requested)
                if not isinstance(chunk, (bytes, bytearray)):
                    raise InputError('上传流必须提供二进制字节')
                if not chunk:
                    raise InputError('上传未完成：实际文件长度小于 Content-Length，请重新选择文件')
                if len(chunk) > requested:
                    raise InputError('上传流返回的数据超过请求长度')
                handle.write(chunk)
                remaining -= len(chunk)
        return path
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
