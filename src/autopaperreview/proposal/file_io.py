"""Atomic proposal output and input identity checks for local commands."""

from __future__ import annotations

import hashlib
import os
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

    def publish(self, content: str) -> None:
        self.check()
        temp = stage_output(self.directory, content)
        try:
            self.check()
            os.replace(temp, self.path.name, src_dir_fd=self.directory, dst_dir_fd=self.directory)
            self.check()
        finally:
            remove_temporary(self.directory, temp)


@contextmanager
def _pinned_output(path: Path):
    absolute = _absolute(path)
    if path.expanduser().resolve() != absolute:
        raise ValueError(f"{_ALIAS}; output paths cannot traverse symbolic links")
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
        active.publish(content)
    else:
        with _pinned_output(path) as output:
            output.publish(content)
