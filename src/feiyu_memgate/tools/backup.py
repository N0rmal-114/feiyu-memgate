"""Backup tools: export memories from OpenViking and seal them into one encrypted bundle.

Standard library only; the encryption lives in `crypto` (which needs the optional `cryptography` package).
User-facing messages go through an `out` callable and never carry secrets or file contents.
"""
import hashlib
import io
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
import zlib
from pathlib import Path

from . import crypto

EXPORT_TIMEOUT = 300
MEMORY_URI = "viking://~/memories"
PACK_PATH = "/api/v1/pack/export"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect, for any method: the bearer key must not travel to another place."""

    def redirect_request(self, *args, **kwargs):
        return None


class ExportError(OSError):
    """The export is unusable (bad archive, unsafe path, empty); the previous export stays untouched."""


def _extract(data, target):
    """Validate the zip and extract it into `target` (a fresh directory). Raises ExportError."""
    target = Path(target)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExportError("导出包不是有效的 zip") from None
    with archive:
        base = target.resolve()
        members = archive.infolist()
        for member in members:
            if not (target / member.filename).resolve().is_relative_to(base):
                raise ExportError("导出包里有不安全的路径")
        if not any(not m.is_dir() for m in members):
            raise ExportError("导出包是空的")
        try:
            for member in members:
                path = (target / member.filename).resolve()
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(member))
        except (zipfile.BadZipFile, zlib.error, EOFError, ValueError, NotImplementedError, RuntimeError):
            # CRC and decompression failures of a truncated or damaged archive surface here
            raise ExportError("导出包已损坏") from None
    return sum(1 for p in target.rglob("*") if p.is_file())


def _tmp_dir(dest, tmp_root):
    """Per-export staging area: a sub-directory of `tmp_root` named after a short hash of the resolved path."""
    name = hashlib.sha256(str(Path(dest).resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(tmp_root) / ("export-" + name)


def _check_tmp_root(dest, tmp_root):
    if Path(tmp_root).resolve().is_relative_to(Path(dest).resolve()):
        raise ExportError("临时目录不能放在导出目录里面")


def recover_export(dest, tmp_root):
    """Undo an interrupted swap and clear stale staging. Runs at the start of every export.

    If the canonical export is missing and an old copy is waiting in the temp area, the old copy goes
    back. Whatever else is in the temp area is stale and removed.
    """
    dest, tmp = Path(dest), _tmp_dir(dest, tmp_root)
    if not tmp.exists():
        return
    target = dest / "memories"
    olds = sorted((p for p in tmp.glob("old-*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
    if not target.exists() and olds:
        dest.mkdir(parents=True, exist_ok=True)
        olds.pop().rename(target)
    for path in tmp.iterdir():
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _download(url, api_key, uri, timeout):
    request = urllib.request.Request(
        url.rstrip("/") + PACK_PATH,
        data=json.dumps({"uri": uri}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        error.close()
        # no response body in the message: it could hold memory text
        raise ExportError(f"OpenViking 返回 {error.code}") from None
    except urllib.error.URLError:
        raise ExportError("连不上 OpenViking") from None


def export_openviking(url, api_key, dest, *, tmp_root, timeout=EXPORT_TIMEOUT, uri=MEMORY_URI, out=print):
    """Download, validate and extract into a temp directory outside `dest`, then swap it in.

    Writes `<dest>/memories/` and `<dest>/memories.ovpack`; returns the number of files. `tmp_root` must be
    outside `dest`. Any failure before the swap leaves the previous export exactly as it was; an interrupted
    swap is repaired by the next call. Raises ExportError / OSError.
    """
    dest = Path(dest)
    _check_tmp_root(dest, tmp_root)
    recover_export(dest, tmp_root)
    data = _download(url, api_key, uri, timeout)

    dest.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_dir(dest, tmp_root)
    tmp.mkdir(parents=True, exist_ok=True)
    target = dest / "memories"
    staging = Path(tempfile.mkdtemp(prefix="new-", dir=tmp))
    old = tmp / ("old-" + uuid.uuid4().hex)
    pack_tmp = tmp / ("pack-" + uuid.uuid4().hex)
    try:
        count = _extract(data, staging)
        pack_tmp.write_bytes(data)
        moved_old = False
        if target.exists():
            target.rename(old)
            moved_old = True
        try:
            staging.rename(target)
        except OSError:
            if moved_old:
                old.rename(target)
            raise
        os.replace(pack_tmp, dest / "memories.ovpack")
        if moved_old:
            shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        pack_tmp.unlink(missing_ok=True)
    out(f"{dest.name}：导出 {count} 个文件，{len(data)} 字节")
    return count


def _check_key_location(dirs, bundle, key_path):
    """The key must never travel with the data or the bundle."""
    key = Path(key_path).resolve()
    for folder in dirs.values():
        if key.is_relative_to(Path(folder).resolve()):
            raise RuntimeError("备份密钥不能放在要备份的目录里面，换一个位置")
    if key.is_relative_to(Path(bundle).resolve().parent):
        raise RuntimeError("备份密钥不能放在加密包所在的目录里面，换一个位置")


def _write_atomic(path, blob):
    """Write via a temp file next to `path`, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    try:
        staged.write_bytes(blob)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def seal(dirs, bundle, key_path, *, state_file=None, skip=(), out=print):
    """Encrypt the directories in `dirs` ({name: Path}) into `bundle`, unless nothing changed.

    Returns "written", "unchanged" or "nothing" (no directory exists). Raises RuntimeError when the library is
    missing, the key file is bad or misplaced, or the encryption fails.
    """
    bundle = Path(bundle)
    state = Path(state_file) if state_file else bundle.with_name(bundle.name + ".state")
    if not any(Path(folder).is_dir() for folder in dirs.values()):
        return "nothing"
    _check_key_location(dirs, bundle, key_path)
    try:
        key, created = crypto.load_or_create_key(key_path)
    except crypto.KeyFileError as error:
        raise RuntimeError(f"备份密钥有问题，没有备份：{error}") from None
    if created:
        out(f"已生成备份密钥，文件在 {key_path}。请马上另存一份到密码管理器或 U 盘，"
            "丢了这把密钥就打不开备份了。")
    try:
        digest = crypto.digest_dirs(dirs, skip=skip)
        # state: line 1 = content digest, line 2 = sha256 of the bundle written then. Skip only when both
        # still match, and never right after the key was (re)generated: the old bundle needs the old key.
        try:
            lines = state.read_text(encoding="utf-8").split()
        except OSError:
            lines = []
        unchanged = False
        if not created and len(lines) == 2 and lines[0] == digest and bundle.is_file():
            current = bundle.read_bytes()
            # the bundle must also open with the key in use now (a failed run, or another key file)
            unchanged = hashlib.sha256(current).hexdigest() == lines[1] and crypto.can_open(current, key)
        if unchanged:
            out("导出没有改动，沿用原来的加密包")
            return "unchanged"
        blob = crypto.encrypt_dirs(dirs, key)
        _write_atomic(bundle, blob)
        _write_atomic(state, (digest + "\n" + hashlib.sha256(blob).hexdigest() + "\n").encode("ascii"))
        out(f"已加密备份：{len(blob)} 字节")
        return "written"
    except OSError as error:
        raise RuntimeError(f"加密备份失败：{type(error).__name__}") from None


def open_bundle(bundle, out_dir, key_path):
    """Decrypt `bundle` into `out_dir` (absent or empty); returns the number of files written."""
    key = crypto.read_key(key_path)
    blob = Path(bundle).read_bytes()
    return crypto.decrypt_to(blob, key, out_dir)
