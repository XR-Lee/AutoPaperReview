"""Atomic proposal output and input identity checks for local commands."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
from pathlib import Path
from uuid import uuid4


_ACTIVE_OUTPUTS = ContextVar("proposal_cli_outputs", default=())
_ALIAS = "output must be distinct from every input file"


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


def _within(parent: Path, child: Path) -> bool:
    parent = _absolute(parent)
    child = _absolute(child)
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
    """Refuse an output that overlaps the repository, checksums, or an input directory."""
    from .repro_metrics import repo_repro_dir

    output = _absolute(path)
    roots = [_repository_root(), repo_repro_dir(), *input_roots]
    for root in roots:
        if root is not None and (_within(root, output) or _within(output, root)):
            raise ValueError("output must not overlap committed repository files or the input directory")
    checksums = repo_repro_dir()
    for name in ("external-sha256.json", "external-hmac-sha256.json", "aggregates.json"):
        if _same_file(output, checksums / name):
            raise ValueError("output must not overlap committed repository files or the input directory")


def rename_exclusive(src_dirfd: int, src_name: str, dst_dirfd: int, dst_name: str) -> None:
    """Move a new file onto a name that must not already exist."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(
        int(src_dirfd),
        os.fsencode(src_name),
        int(dst_dirfd),
        os.fsencode(dst_name),
        1,  # RENAME_NOREPLACE
    ) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise ValueError(_ALIAS)
    raise OSError(error, os.strerror(error), dst_name)


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

    def _destination_identity(self) -> tuple[int, int] | None:
        try:
            info = os.stat(self.path.name, dir_fd=self.directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"{_ALIAS}; use an unlinked regular destination")
        return file_identity(info)

    def check(self) -> None:
        if file_identity(self.path.parent.lstat()) != self.parent_identity:
            raise ValueError("output directory changed while preparing the result")
        destination = self._destination_identity()
        for path, identity in self.inputs.items():
            if destination == identity:
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
        identity = file_identity(info)
        if _absolute(path) == self.path or self._destination_identity() == identity:
            raise ValueError(_ALIAS)
        if self.inputs.setdefault(_absolute(path), identity) != identity:
            raise ValueError("input file identity changed while preparing the result")

    def publish(self, content: str, *, replace: bool = False) -> None:
        """Publish content from a fresh directory that cannot be an input file.

        A new destination is published with a non-replacing rename. An existing
        file is rewritten through the file descriptor opened after the identity
        check, so an input moved onto that name is left unchanged.
        """
        self.check()
        original = self._destination_identity()
        if original is not None and not replace:
            raise ValueError(_ALIAS)
        if original is not None and original in self.inputs.values():
            raise ValueError(_ALIAS)
        staging = Path(tempfile.mkdtemp(prefix="apr-output-"))
        staging_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            staging_identity = file_identity(os.fstat(staging_fd))
            if staging_identity == self.parent_identity or staging_identity in self.inputs.values():
                raise ValueError(_ALIAS)
            temp = stage_output(staging_fd, content)
            self.check()
            current = self._destination_identity()
            if current != original or (current is not None and current in self.inputs.values()):
                raise ValueError(_ALIAS)
            if current is None:
                rename_exclusive(staging_fd, temp, self.directory, self.path.name)
            else:
                self._rewrite_verified(staging_fd, temp, original)
            self.check()
        finally:
            os.close(staging_fd)
            shutil.rmtree(staging, ignore_errors=True)

    def _rewrite_verified(self, staging_fd: int, temp_name: str, original: tuple[int, int]) -> None:
        """Write into the already-open destination inode. Never unlink a swapped path."""
        try:
            dest = os.open(self.path.name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=self.directory)
        except OSError as exc:
            raise ValueError(_ALIAS) from exc
        try:
            info = os.fstat(dest)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or file_identity(info) != original
                or original in self.inputs.values()
            ):
                raise ValueError(_ALIAS)
            with os.fdopen(os.open(temp_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=staging_fd), "rb") as source:
                payload = source.read()
            os.ftruncate(dest, 0)
            offset = 0
            while offset < len(payload):
                offset += os.write(dest, payload[offset:])
            os.fsync(dest)
            if self._destination_identity() != original:
                raise ValueError(_ALIAS)
        finally:
            os.close(dest)


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
