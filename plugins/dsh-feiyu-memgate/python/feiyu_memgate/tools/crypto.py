"""Encrypt memory exports into one bundle before they are stored or shared.

File format: MAGIC + 12 random bytes (nonce) + AES-256-GCM(zip data), with MAGIC as the associated data.
The key is 32 random bytes in a file the caller names (never inside the data that is backed up).
Key bytes are never printed or logged.
"""
import hashlib
import io
import os
import posixpath
import shutil
import tempfile
import unicodedata
import zipfile
import zlib
from pathlib import Path

MAGIC = b"FYMB1"
NONCE_LEN = 12
KEY_LEN = 32
_ZIP_DATE = (2020, 1, 1, 0, 0, 0)  # fixed, so the zip bytes depend only on names and content


class KeyFileError(OSError):
    """The key file can't be read, created, or has the wrong size."""


class DecryptError(ValueError):
    """The bundle can't be opened: wrong magic, wrong key, tampered, or unsafe content."""


class CryptoMissing(RuntimeError):
    """The host Python has no cryptography library."""


def _aesgcm(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import-not-found]  # optional extra
    except ImportError:
        raise CryptoMissing("主机 Python 缺少 cryptography 库，装好再备份："
                            "python -m pip install cryptography") from None
    if len(key) != KEY_LEN:
        raise KeyFileError(f"密钥长度不对，应该是 {KEY_LEN} 个字节")
    return AESGCM(key)


def read_key(path):
    """Read an existing key file; never creates one."""
    path = Path(path)
    try:
        key = path.read_bytes()
    except OSError as error:
        raise KeyFileError(f"读不了密钥文件 {path}：{type(error).__name__}") from None
    if len(key) != KEY_LEN:
        raise KeyFileError(f"密钥文件 {path} 的长度不对，应该是 {KEY_LEN} 个字节")
    return key


def load_or_create_key(path):
    """Return (key, created). A missing file is created with a fresh random key, owner-only where supported."""
    path = Path(path)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The 0o600 mode is honoured on POSIX; on Windows it does not set ACLs, the folder's own rights apply.
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return read_key(path), False
    except OSError as error:
        raise KeyFileError(f"建不了密钥文件 {path}：{type(error).__name__}") from None
    key = os.urandom(KEY_LEN)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
    except OSError as error:
        try:
            path.unlink()
        except OSError:
            pass
        raise KeyFileError(f"写不进密钥文件 {path}：{type(error).__name__}") from None
    return key, True


def _entries(dirs):
    """Sorted [(name, path)] of every file under each existing dir; name is <prefix>/<relative path>."""
    found = []
    for prefix, folder in dirs.items():
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.is_file():
                found.append((prefix + "/" + path.relative_to(folder).as_posix(), path))
    return sorted(found, key=lambda item: item[0])


def digest_dirs(dirs, skip=()):
    """sha256 over the sorted (name, bytes) pairs of every file, leaving out the files whose path inside their
    folder is in `skip`."""
    digest = hashlib.sha256()
    for name, path in _entries(dirs):
        if name.split("/", 1)[1] in skip:
            continue
        data = path.read_bytes()
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big") + encoded + len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def encrypt_dirs(dirs, key):
    aead = _aesgcm(key)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in _entries(dirs):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    nonce = os.urandom(NONCE_LEN)
    return MAGIC + nonce + aead.encrypt(nonce, buffer.getvalue(), MAGIC)


def can_open(blob, key):
    """True when `key` authenticates `blob`. In memory only: nothing is written or printed."""
    if len(key) != KEY_LEN or not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + NONCE_LEN + 16:
        return False
    aead = _aesgcm(key)
    try:
        aead.decrypt(blob[len(MAGIC):len(MAGIC) + NONCE_LEN], blob[len(MAGIC) + NONCE_LEN:], MAGIC)
    except Exception:  # noqa: BLE001 - any failure to authenticate means the key does not open the blob
        return False
    return True


# Names Windows treats as devices whatever the extension ("con.txt" is the console too).
_DEVICES = ({"con", "prn", "aux", "nul", "conin$", "conout$"}
            | {"com%d" % i for i in range(10)} | {"lpt%d" % i for i in range(10)}
            | {"com¹", "com²", "com³", "lpt¹", "lpt²", "lpt³"})


def _check_member_name(name):
    """Refuse anything that could land outside the output folder, or be written somewhere else than
    its name says on Windows: a part ending in a dot or a space (Windows drops it, so "notes." and
    "notes" become one file) or naming a device (CON, NUL, COM1 ...)."""
    bare = name[:-1] if name.endswith("/") else name
    if not bare or "\\" in name or name.startswith("/") or ":" in name or "\0" in name:
        raise DecryptError("加密包里有不安全的文件名")
    if any(part in ("", ".", "..") for part in bare.split("/")):
        raise DecryptError("加密包里有不安全的文件名")
    if posixpath.isabs(bare) or posixpath.normpath(bare) != bare:
        raise DecryptError("加密包里有不安全的文件名")
    for part in bare.split("/"):
        if part[-1] in " ." or part.split(".", 1)[0].rstrip(" ").lower() in _DEVICES:
            raise DecryptError("加密包里有不安全的文件名")


def _check_member_names(members):
    """Check every name, and that no two members are one path on a case-insensitive file system
    (Windows, macOS): the second would silently overwrite the first. Nothing is written before this."""
    seen = set()
    for member in members:
        name = member.filename
        _check_member_name(name)
        key = unicodedata.normalize("NFC", name[:-1] if name.endswith("/") else name).casefold()
        if key in seen:
            raise DecryptError("加密包里有重名的文件（不分大小写）")
        seen.add(key)


def decrypt_to(blob, key, out_dir):
    """Decrypt `blob` into `out_dir` (absent or empty). Returns the number of files written.

    Authenticates the whole bundle first, checks every name, writes into a temp folder beside
    `out_dir` and renames it into place only when everything worked; on any error nothing is left.
    """
    out_dir = Path(out_dir)
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise DecryptError("输出目录已经存在而且不是空的，换一个空目录")
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + NONCE_LEN + 16:
        raise DecryptError("这不是加密备份包")
    if len(key) != KEY_LEN:
        raise DecryptError("密钥的长度不对")
    aead = _aesgcm(key)
    nonce = blob[len(MAGIC):len(MAGIC) + NONCE_LEN]
    try:
        data = aead.decrypt(nonce, blob[len(MAGIC) + NONCE_LEN:], MAGIC)
    except Exception:  # noqa: BLE001 - wrong key or a changed blob; no detail is passed on
        raise DecryptError("密钥不对，或者加密包被改过") from None
    # Anything the zip reader can raise on an authenticated but odd archive is a decrypt error, not a crash.
    zip_errors = (zipfile.BadZipFile, zlib.error, NotImplementedError, EOFError, ValueError, RuntimeError)
    staging = None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            _check_member_names(members)
            out_dir.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".fym-decrypt-", dir=out_dir.parent))
            base = staging.resolve()
            count = 0
            for member in members:
                path = (staging / member.filename).resolve()
                if not path.is_relative_to(base):
                    raise DecryptError("加密包里有不安全的文件名")
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(member))
                    count += 1
        if out_dir.exists():
            out_dir.rmdir()
        os.replace(staging, out_dir)
    except BaseException as error:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(error, DecryptError) or not isinstance(error, zip_errors):
            raise
        raise DecryptError("加密包里的内容读不出来，可能已损坏") from None
    return count
