"""Atomic proposal output and input identity checks for local commands."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import os
import shutil
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
from pathlib import Path
from uuid import uuid4


_ACTIVE_OUTPUTS = ContextVar("proposal_cli_outputs", default=())
_ALIAS = "output must be distinct from every input file"
_STAMP_LOCK = threading.Lock()
_STAMP = 0
_PUBLISH_LOCKS_GUARD = threading.Lock()
_PUBLISH_LOCKS: dict[tuple, threading.Lock] = {}
_PROTECTED_GUARD = threading.Lock()
# inode -> (ext4 generation, path opened as an input). Rename keeps both the
# inode and the generation. Reusing the inode bumps the generation.
_PROTECTED_FILES: dict[tuple[int, int], tuple[int, Path]] = {}
_FS_IOC_GETVERSION = 0x80087601


def _destination_lock(key: tuple) -> threading.Lock:
    """Serialize threads. flock alone does not: on Linux it is per process."""
    with _PUBLISH_LOCKS_GUARD:
        lock = _PUBLISH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PUBLISH_LOCKS[key] = lock
        return lock


def _generation(path: Path) -> int | None:
    """Filesystem generation for a regular file. Unchanged by rename."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        value = ctypes.c_uint64()
        try:
            fcntl.ioctl(fd, _FS_IOC_GETVERSION, value, True)
        except OSError:
            return None
        return int(value.value)
    finally:
        os.close(fd)


def _protect_inode(info: os.stat_result, path: Path) -> None:
    """Remember a regular input file so a later publish can recognize a move."""
    if not stat.S_ISREG(info.st_mode):
        return
    absolute = _absolute(path)
    generation = _generation(absolute)
    if generation is None:
        return
    with _PROTECTED_GUARD:
        _PROTECTED_FILES[file_identity(info)] = (generation, absolute)


def _protected_alias(info: os.stat_result, destination: Path | None) -> bool:
    """True when replacing this inode would destroy a protected input.

    A later command may overwrite the path where the input still sits. An input
    moved onto another name stays protected: rename keeps the inode and its
    generation. A reused inode has a new generation and is forgotten.
    """
    if destination is None or not stat.S_ISREG(info.st_mode):
        return False
    identity = file_identity(info)
    with _PROTECTED_GUARD:
        record = _PROTECTED_FILES.get(identity)
    if record is None:
        return False
    generation, origin = record
    try:
        current = destination.stat()
    except FileNotFoundError:
        current = None
    found = _generation(destination) if current is not None else None
    if current is None or file_identity(current) != identity or found != generation:
        with _PROTECTED_GUARD:
            if _PROTECTED_FILES.get(identity) == record:
                _PROTECTED_FILES.pop(identity, None)
        return False
    if _absolute(destination) == origin:
        return False
    return True


def _directory_path(directory: int) -> Path:
    return Path(os.readlink(f"/proc/self/fd/{directory}"))


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def file_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def open_output_directory(path: Path) -> int:
    """Open or create each component without following a directory symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=directory)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
    except BaseException:
        os.close(directory)
        raise
    return directory


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _within(parent: Path, child: Path) -> bool:
    parent = _real(parent)
    child = _real(child)
    if child == parent:
        return True
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _same_file(path: Path, other: Path) -> bool:
    try:
        if not path.exists() or not other.is_file():
            return False
        return file_identity(path.stat()) == file_identity(other.stat())
    except OSError:
        return False


def assert_output_isolated(path: Path, *input_roots: Path) -> None:
    """Refuse an output that overlaps tracked repository files, checksums, or an input directory.

    A gitignored review directory such as ``workdir/`` stays writable. Overlap uses
    the real path, so a symlink to an input directory is the same directory.
    """
    from ..public_calibration import PublicCalibrationError, assert_local_only
    from .repro_metrics import repo_repro_dir

    output = _real(Path(path).expanduser())
    try:
        assert_local_only(output)
    except PublicCalibrationError as exc:
        raise ValueError(
            "output must not overlap committed repository files or the input directory"
        ) from exc
    roots = [_real(Path(repo_repro_dir()).expanduser())]
    for root in input_roots:
        if root is not None:
            roots.append(_real(Path(root).expanduser()))
    for root in roots:
        if _within(root, output) or _within(output, root):
            raise ValueError("output must not overlap committed repository files or the input directory")
    checksums = repo_repro_dir()
    for name in ("external-sha256.json", "external-hmac-sha256.json", "aggregates.json"):
        if _same_file(output, checksums / name):
            raise ValueError("output must not overlap committed repository files or the input directory")


RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2


def _renameat2(src_dirfd: int, src_name: str, dst_dirfd: int, dst_name: str, flags: int) -> None:
    """Rename without replacing, or replace a report, and fail closed otherwise.

    A replacing rename refuses a destination whose inode is a protected input.
    The check is inside this function so a caller that moves the input onto the
    name before the real syscall still leaves that input in place.
    """
    if flags == 0:
        try:
            info = os.stat(dst_name, dir_fd=dst_dirfd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None and _protected_alias(info, _directory_path(dst_dirfd) / dst_name):
            raise ValueError(_ALIAS)
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is not available", dst_name)
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(
        int(src_dirfd),
        os.fsencode(src_name),
        int(dst_dirfd),
        os.fsencode(dst_name),
        int(flags),
    ) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST and flags == RENAME_NOREPLACE:
        raise ValueError(_ALIAS)
    if error == errno.EXDEV:
        raise OSError(error, "output must stay on the same filesystem as its staging file", dst_name)
    raise OSError(error, os.strerror(error), dst_name)


def rename_exclusive(src_dirfd: int, src_name: str, dst_dirfd: int, dst_name: str) -> None:
    """Move a new file onto a name that must not already exist."""
    _renameat2(src_dirfd, src_name, dst_dirfd, dst_name, RENAME_NOREPLACE)


def remove_temporary(directory: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def stage_output(directory: int, content: str) -> str:
    name = f".apr-output-{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(name, flags, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        remove_temporary(directory, name)
        raise
    return name


class _Output:
    def __init__(self, path: Path, directory: int):
        self.path = path
        self.directory = directory
        self.parent_identity = file_identity(os.fstat(directory))
        self.inputs: dict[Path, tuple[int, int]] = {}
        self.temporary_roots: set[Path] = set()

    def _destination_info(self) -> os.stat_result | None:
        """Stat the destination. A link count of zero is a rename in progress.

        ``rename`` can leave the name visible with ``st_nlink == 0`` for an
        instant. Another thread's pre-lock check must wait that out. A real
        extra link (``st_nlink > 1``) or a non-regular file still fails closed.
        """
        for _attempt in range(20):
            try:
                info = os.stat(self.path.name, dir_fd=self.directory, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                return info
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                break
            time.sleep(0.001)
        raise ValueError(f"{_ALIAS}; use an unlinked regular destination")

    def _destination_identity(self) -> tuple[int, int] | None:
        info = self._destination_info()
        return None if info is None else file_identity(info)

    def _blocked(self, info: os.stat_result | None) -> bool:
        if info is None:
            return False
        if file_identity(info) in self.inputs.values():
            return True
        return _protected_alias(info, self.path)

    def check(self) -> None:
        if file_identity(self.path.parent.lstat()) != self.parent_identity:
            raise ValueError("output directory changed while preparing the result")
        destination = self._destination_info()
        if self._blocked(destination):
            raise ValueError(_ALIAS)
        for path, identity in self.inputs.items():
            if destination is not None and file_identity(destination) == identity:
                raise ValueError(_ALIAS)
            try:
                current = path.stat()
            except FileNotFoundError:
                if any(path.is_relative_to(root) for root in self.temporary_roots):
                    continue
                raise ValueError("input file disappeared while preparing the result") from None
            if file_identity(current) != identity:
                raise ValueError("input file identity changed while preparing the result")

    def remember(self, path: Path, info: os.stat_result) -> None:
        # Dict insertion is shared by copied worker contexts. setdefault keeps
        # the original identity even if another worker sees a replacement.
        # The process-wide digest lets a later writer refuse the same bytes
        # after the original path has been moved onto the destination.
        identity = file_identity(info)
        if _absolute(path) == self.path or self._destination_identity() == identity:
            raise ValueError(_ALIAS)
        _protect_inode(info, path)
        if self.inputs.setdefault(_absolute(path), identity) != identity:
            raise ValueError("input file identity changed while preparing the result")

    def publish(self, content: str, *, replace: bool = False) -> None:
        """Publish one complete file while holding the destination lock.

        The lock covers validation, the temporary write, fsync, and the rename.
        A new file still comes from a fresh directory. An existing report is
        replaced only when its inode is not a protected input. Nothing here
        moves or restores an input. A short write deletes only its temporary file.
        """
        lock = _destination_lock((*self.parent_identity, self.path.name))
        with lock:
            self._publish_locked(content, replace=replace)

    def _publish_locked(self, content: str, *, replace: bool) -> None:
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        lock_fd = os.open(".apr-publish.lock", flags, 0o600, dir_fd=self.directory)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                self._publish_critical(content, replace=replace)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
            try:
                os.unlink(".apr-publish.lock", dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def _publish_critical(self, content: str, *, replace: bool) -> None:
        self.check()
        info = self._destination_info()
        original = None if info is None else file_identity(info)
        if info is not None and not replace:
            raise ValueError(_ALIAS)
        if self._blocked(info):
            raise ValueError(_ALIAS)
        if info is None:
            self._publish_fresh(content, original)
            return
        name = f".apr-output-{uuid4().hex}.tmp"
        created = None
        try:
            created = _write_complete(self.directory, name, content.encode("utf-8"), self.path.name)
            self.check()
            current = self._destination_info()
            if current is None or file_identity(current) != original or self._blocked(current):
                raise ValueError(_ALIAS)
            _renameat2(self.directory, name, self.directory, self.path.name, 0)
            os.fsync(self.directory)
            self.check()
        finally:
            self._unlink_ours(name, created)

    def _publish_fresh(self, content: str, original: tuple[int, int] | None) -> None:
        staging = Path(tempfile.mkdtemp(prefix="apr-output-"))
        staging_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            staging_identity = file_identity(os.fstat(staging_fd))
            if staging_identity == self.parent_identity or staging_identity in self.inputs.values():
                raise ValueError(_ALIAS)
            temp = stage_output(staging_fd, content)
            self.check()
            current = self._destination_info()
            current_id = None if current is None else file_identity(current)
            if current_id != original or self._blocked(current):
                raise ValueError(_ALIAS)
            rename_exclusive(staging_fd, temp, self.directory, self.path.name)
            os.fsync(self.directory)
            self.check()
        finally:
            os.close(staging_fd)
            shutil.rmtree(staging, ignore_errors=True)

    def _unlink_ours(self, name: str, created: tuple[int, int] | None) -> None:
        """Delete this writer's temporary file, never the destination or an input."""
        if created is None:
            return
        try:
            info = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if file_identity(info) != created or _protected_alias(info, _directory_path(self.directory) / name):
            return
        try:
            dest = os.stat(self.path.name, dir_fd=self.directory, follow_symlinks=False)
        except FileNotFoundError:
            dest = None
        if dest is not None and file_identity(dest) == created:
            return
        remove_temporary(self.directory, name)


def _stamp_fresh(fd: int, directory: int, beside: str) -> None:
    """Give a new report a mtime strictly after every file already published."""
    global _STAMP
    try:
        floor = os.stat(beside, dir_fd=directory, follow_symlinks=False).st_mtime_ns
    except FileNotFoundError:
        floor = 0
    with _STAMP_LOCK:
        _STAMP = max(_STAMP, time.time_ns(), floor) + 1
        stamp = _STAMP
    os.utime(fd, ns=(stamp, stamp))


def _write_complete(directory: int, name: str, payload: bytes, beside: str) -> tuple[int, int]:
    """Write every byte to a new file and fsync it. A short write deletes that file only."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(name, flags, 0o600, dir_fd=directory)
    try:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            wrote = os.write(fd, view[offset:])
            if wrote <= 0:
                raise OSError(errno.ENOSPC, "No space left on device")
            offset += wrote
        os.fsync(fd)
        _stamp_fresh(fd, directory, beside)
        os.fsync(fd)
        return file_identity(os.fstat(fd))
    except BaseException:
        remove_temporary(directory, name)
        raise
    finally:
        os.close(fd)


@contextmanager
def _pinned_output(path: Path):
    absolute = _absolute(path)
    if path.expanduser().resolve() != absolute:
        raise ValueError(f"{_ALIAS}; output paths cannot traverse symbolic links")
    assert_output_isolated(absolute)
    try:
        expected = file_identity(absolute.parent.lstat())
    except FileNotFoundError:
        expected = None
    directory = open_output_directory(absolute.parent)
    try:
        output = _Output(absolute, directory)
        if expected is not None and output.parent_identity != expected:
            raise ValueError("output directory changed while preparing the result")
        output.check()
        yield output
    finally:
        os.close(directory)


@contextmanager
def protected_output(path: Path | None):
    if path is None:
        yield
        return
    with _pinned_output(path) as output:
        token = _ACTIVE_OUTPUTS.set((*_ACTIVE_OUTPUTS.get(), output))
        try:
            yield
        finally:
            _ACTIVE_OUTPUTS.reset(token)


def protect_cli_output(dispatch):
    @wraps(dispatch)
    def run(args):
        path = getattr(args, "output", None)
        with protected_output(local_output(path) if path is not None else None):
            return dispatch(args)

    return run


def local_output(path: Path) -> Path:
    from ..public_calibration import assert_local_only

    absolute = _absolute(path)
    if assert_local_only(path) != absolute:
        raise ValueError(f"{_ALIAS}; output paths cannot traverse symbolic links")
    return absolute


def map_with_input_tracking(executor, function, items):
    futures = [executor.submit(copy_context().run, function, item) for item in items]
    return [future.result() for future in futures]


@contextmanager
def temporary_input_dir(*, prefix: str):
    with tempfile.TemporaryDirectory(prefix=prefix) as folder:
        directory = Path(folder).resolve()
        for active in _ACTIVE_OUTPUTS.get():
            active.temporary_roots.add(directory)
        yield directory


def _active_output(path: Path) -> _Output | None:
    absolute = _absolute(path)
    return next((out for out in reversed(_ACTIVE_OUTPUTS.get()) if out.path == absolute), None)


def require_distinct_output(output: Path, *inputs: Path) -> None:
    """Check declared inputs early, including those opened by native runtimes."""
    active = _active_output(output)
    for path in inputs:
        if output.resolve() == path.resolve():
            raise ValueError(_ALIAS)
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        if active is not None:
            active.remember(path, info)
        else:
            try:
                destination = output.stat()
            except FileNotFoundError:
                continue
            if file_identity(destination) == file_identity(info):
                raise ValueError(_ALIAS)


@contextmanager
def open_input(path: Path, mode: str = "r"):
    with path.open(mode, encoding=None if mode == "rb" else "utf-8") as handle:
        remember_input(path, handle)
        yield handle


def remember_input(path: Path, handle) -> None:
    active = _ACTIVE_OUTPUTS.get()
    if active:
        info = os.fstat(handle.fileno())
        for output in active:
            output.remember(path, info)


def read_text(path: Path) -> str:
    with open_input(path) as handle:
        return handle.read()


def read_bytes(path: Path) -> bytes:
    with open_input(path, "rb") as handle:
        return handle.read()


def input_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open_input(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, content: str) -> None:
    active = _active_output(path)
    if active is not None:
        # Rewrite only the inode checked before the write. A swapped input is not unlinked.
        active.publish(content, replace=True)
    else:
        with _pinned_output(path) as output:
            output.publish(content, replace=True)
