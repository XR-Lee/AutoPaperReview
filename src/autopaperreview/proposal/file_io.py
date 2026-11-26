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
    """Rename without replacing, or exchange two names, and fail closed otherwise."""
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
        """Publish a complete file, then make its name visible.

        A new destination uses a non-replacing rename from a fresh directory.
        An existing report is exchanged with a complete temporary file in the
        same directory. A short write leaves the previous file untouched, and
        an input moved onto the destination name is not unlinked.
        """
        self.check()
        original = self._destination_identity()
        if original is not None and not replace:
            raise ValueError(_ALIAS)
        if original is not None and original in self.inputs.values():
            raise ValueError(_ALIAS)
        if original is not None:
            self._exchange_verified(content, original)
            self.check()
            return
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
            rename_exclusive(staging_fd, temp, self.directory, self.path.name)
            self.check()
        finally:
            os.close(staging_fd)
            shutil.rmtree(staging, ignore_errors=True)

    def _exchange_verified(self, content: str, original: tuple[int, int]) -> None:
        """Replace an existing file only after the new bytes are complete."""
        name = f".apr-output-{uuid4().hex}.tmp"
        created = _write_complete(self.directory, name, content.encode("utf-8"))
        held = None
        try:
            self.check()
            current = self._destination_identity()
            if current != original or current in self.inputs.values():
                raise ValueError(_ALIAS)
            # Keep the pre-exchange inode open so a later writer cannot reuse
            # its number and look like the file this writer displaced.
            held = os.open(
                self.path.name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=self.directory,
            )
            if file_identity(os.fstat(held)) != original:
                raise ValueError(_ALIAS)
            _renameat2(self.directory, name, self.directory, self.path.name, RENAME_EXCHANGE)
            self._settle_exchange(name, created, held)
        except BaseException:
            try:
                info = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None and file_identity(info) == created:
                remove_temporary(self.directory, name)
            raise
        finally:
            if held is not None:
                os.close(held)

    def _name_identity(self, name: str):
        try:
            return file_identity(os.stat(name, dir_fd=self.directory, follow_symlinks=False))
        except FileNotFoundError:
            return None

    def _exchange_back(self, name: str) -> None:
        _renameat2(self.directory, name, self.directory, self.path.name, RENAME_EXCHANGE)

    def _drop_temp(self, name: str, created: tuple[int, int]) -> None:
        """Remove this writer's leftover, never the live report or an input."""
        ident = self._name_identity(name)
        if ident is None:
            return
        if ident == created or (
            ident != self._destination_identity() and ident not in self.inputs.values()
        ):
            remove_temporary(self.directory, name)

    def _settle_exchange(self, name: str, created: tuple[int, int], held: int) -> None:
        """Keep a newer report, and put a displaced input back on this name.

        ``os.rename`` replaces the destination inode, so a writer who lands
        inside that call cannot be put back. Exchange back instead. If that
        exchange itself swaps in a newer writer, one more exchange restores
        the newest file. An input moved onto this name is exchanged back and
        only this writer's temporary file is removed.
        """
        destination = self._destination_identity()
        swapped = self._name_identity(name)
        original = file_identity(os.fstat(held))
        if (
            destination == created
            and swapped == original
            and swapped not in self.inputs.values()
        ):
            os.unlink(name, dir_fd=self.directory)
            return
        if destination == created and swapped is not None:
            self._exchange_back(name)
            if self._name_identity(name) == created:
                remove_temporary(self.directory, name)
                raise ValueError(_ALIAS)
            # A publisher landed inside the rollback and was swapped here.
            self._exchange_back(name)
        self._drop_temp(name, created)
        raise ValueError(_ALIAS)


def _write_complete(directory: int, name: str, payload: bytes) -> tuple[int, int]:
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
