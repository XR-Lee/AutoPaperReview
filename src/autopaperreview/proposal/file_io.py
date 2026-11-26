"""Atomic proposal output and input identity checks for local commands."""

from __future__ import annotations

import ctypes
import errno
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
# inode -> (generation or None when the filesystem cannot report one, path
# opened as an input). Rename keeps the inode. Reusing the inode bumps a
# generation the filesystem can report. None is still a record: an unreadable
# generation must not drop the input.
_PROTECTED_FILES: dict[tuple[int, int], tuple[int | None, Path]] = {}
_FS_IOC_GETVERSION = 0x80087601


def _destination_lock(key: tuple) -> threading.Lock:
    """Serialize threads. flock alone does not: on Linux it is per process."""
    with _PUBLISH_LOCKS_GUARD:
        lock = _PUBLISH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PUBLISH_LOCKS[key] = lock
        return lock


def _generation_fd(fd: int) -> int | None:
    """Filesystem generation for the inode behind ``fd``. Unchanged by rename."""
    import fcntl

    try:
        info = os.fstat(fd)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    value = ctypes.c_uint64()
    try:
        fcntl.ioctl(fd, _FS_IOC_GETVERSION, value, True)
    except OSError:
        return None
    return int(value.value)


def _generation(path: Path) -> int | None:
    """Filesystem generation for a regular file. Unchanged by rename."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        return _generation_fd(fd)
    finally:
        os.close(fd)


def _generation_if_same(path: Path, info: os.stat_result) -> int | None:
    """Generation of ``info`` only. A different inode at ``path`` is not a reading."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        current = os.fstat(fd)
        if file_identity(current) != file_identity(info):
            return None
        return _generation_fd(fd)
    finally:
        os.close(fd)


def _protect_inode(info: os.stat_result, path: Path) -> None:
    """Remember a regular input file so a later publish can recognize a move.

    A filesystem that cannot report a generation still leaves a record. The
    overwrite decision does not depend on that record: a regular file that is
    not a protected input from another path may be replaced.
    """
    if not stat.S_ISREG(info.st_mode):
        return
    absolute = _absolute(path)
    generation = _generation(absolute)
    with _PROTECTED_GUARD:
        _PROTECTED_FILES[file_identity(info)] = (generation, absolute)


def _live_record(info: os.stat_result, found: int | None):
    """Return the protection record, dropping it only when reuse is proven.

    Both generations have to be readable and different. An unreadable
    generation is not reuse, and it does not forget the input.
    """
    if not stat.S_ISREG(info.st_mode):
        return None
    identity = file_identity(info)
    with _PROTECTED_GUARD:
        record = _PROTECTED_FILES.get(identity)
    if record is None:
        return None
    recorded, _origin = record
    if recorded is None or found is None or recorded == found:
        return record
    with _PROTECTED_GUARD:
        if _PROTECTED_FILES.get(identity) == record:
            _PROTECTED_FILES.pop(identity, None)
    return None


def _inode_protected(info: os.stat_result, found: int | None) -> bool:
    """True when this inode is still a protected input, whatever its path."""
    return _live_record(info, found) is not None


def _blocks_unlink(info: os.stat_result, found: int | None, output: Path) -> bool:
    """True when deleting this inode would destroy an input from another path.

    The path where the input still sits may be rewritten. A rename onto the
    output, or onto the temporary name, still blocks the unlink.
    """
    record = _live_record(info, found)
    if record is None:
        return False
    _generation_value, origin = record
    return _absolute(output) != origin


def _stolen_input(info: os.stat_result, path: Path, destination: Path) -> bool:
    """True when this inode is a protected input that did not start at ``destination``.

    An input moved onto the output name keeps its inode and generation. The
    output path itself may be rewritten. A generation that cannot be read stays
    protected. A reused inode has a new generation and is not the old input.
    """
    record = _live_record(info, _generation_if_same(path, info))
    if record is None:
        return False
    _generation_value, origin = record
    return _absolute(destination) != origin


def _protected_alias(info: os.stat_result, destination: Path | None) -> bool:
    """True when replacing this inode would destroy a protected input.

    A later command may overwrite the path where the input still sits. An input
    moved onto another name stays protected. A reused inode is forgotten only
    when both generations are readable and differ.
    """
    if destination is None or not stat.S_ISREG(info.st_mode):
        return False
    record = _live_record(info, _generation_if_same(destination, info))
    if record is None:
        return False
    _generation_value, origin = record
    try:
        current = os.stat(destination, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if file_identity(current) != file_identity(info):
        return False
    return _absolute(destination) != origin


def _empty_directory(directory: int) -> None:
    """Unlink every name in an open directory. Do not follow a symlink."""
    for name in os.listdir(directory):
        info = os.lstat(name, dir_fd=directory)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                _empty_directory(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory)
        else:
            os.unlink(name, dir_fd=directory)


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


def _same_report(info: os.stat_result, expected: tuple[int, int], fd: int, output: Path) -> bool:
    """True when ``fd`` is the regular inode ``expected`` and not an input."""
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink >= 1
        and file_identity(info) == expected
        and not _blocks_unlink(info, _generation_fd(fd), output)
    )


def _unlink_if_same(
    directory: int,
    name: str,
    expected: tuple[int, int],
    *,
    output: Path | None = None,
) -> bool:
    """Unlink ``name`` only when a fresh ``fstat`` is still ``expected``.

    A protected inode is left in place. An unmarked legacy report is not
    protected by this check.
    """
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or file_identity(info) != expected:
            return False
        found = _generation_fd(fd)
        if output is None:
            blocked = _inode_protected(info, found)
        else:
            blocked = _blocks_unlink(info, found, output)
        if blocked:
            return False
    finally:
        os.close(fd)
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _unlink_created(directory: int, name: str, fd: int) -> None:
    """Delete the inode behind ``fd`` when ``name`` still refers to it."""
    try:
        expected = file_identity(os.fstat(fd))
    except OSError:
        return
    _unlink_if_same(directory, name, expected)


def _renameat2(src_dirfd: int, src_name: str, dst_dirfd: int, dst_name: str, flags: int) -> None:
    """Rename without replacing, or exchange a regular file, and fail closed otherwise.

    The destination check runs for every flag, including ``RENAME_NOREPLACE``
    and ``RENAME_EXCHANGE``. A check limited to a plain rename never saw the
    flags this publisher uses. The check is inside this function so a caller
    that moves an input onto the name before the real syscall still leaves
    that input in place. An unmarked file is not refused: the overwrite
    decision is the round-9 rule, a regular file that is not a protected input.
    """
    try:
        info = os.stat(dst_name, dir_fd=dst_dirfd, follow_symlinks=False)
    except FileNotFoundError:
        info = None
    if info is not None:
        dest = _directory_path(dst_dirfd) / dst_name
        if not stat.S_ISREG(info.st_mode) or _protected_alias(info, dest):
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
    created = file_identity(os.fstat(fd))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _unlink_if_same(directory, name, created)
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

        The lock is the destination directory, so deleting a lock file cannot
        split two publishers onto different inodes. A new file is staged in
        the destination directory. An existing regular file is exchanged when
        it is not a protected input. A protected input left on the destination
        or moved onto the temporary name stays on the destination. A short
        write deletes only its own temporary inode.
        """
        lock = _destination_lock((*self.parent_identity, self.path.name))
        with lock:
            self._publish_locked(content, replace=replace)

    def _publish_locked(self, content: str, *, replace: bool) -> None:
        # flock is on the directory the caller already holds open. Deleting or
        # replacing a lock file cannot give a second process a different inode.
        # Imported here so the main CLI can load on a platform without fcntl.
        import fcntl

        fcntl.flock(self.directory, fcntl.LOCK_EX)
        try:
            self._publish_critical(content, replace=replace)
        finally:
            fcntl.flock(self.directory, fcntl.LOCK_UN)

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
            self._replace_existing(name, created, original)
            os.fsync(self.directory)
            self.check()
        finally:
            self._unlink_ours(name, created)

    def _replace_existing(self, name: str, created: tuple[int, int], original: tuple[int, int]) -> None:
        """Exchange a finished temp file with the current regular report.

        The exchange is one ``renameat2`` call, and that call refuses a
        protected destination before the syscall. An unmarked legacy report
        is a normal destination. After the call returns, a protected input
        sitting on the destination stays there. A protected input sitting on
        the temporary name is moved back onto the destination. The unlink
        checks the inode again. If ``renameat2`` is unavailable the previous
        report stays.
        """
        dest_fd = os.open(self.path.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=self.directory)
        try:
            held = os.fstat(dest_fd)
            held_id = file_identity(held)
            if held_id != original or self._blocked(held):
                raise ValueError(_ALIAS)
            _renameat2(self.directory, name, self.directory, self.path.name, RENAME_EXCHANGE)
            self._settle_exchange(name, created, held_id)
        finally:
            os.close(dest_fd)

    def _settle_exchange(self, name: str, created: tuple[int, int], held_id: tuple[int, int]) -> None:
        """Keep a protected input on the destination name and drop only our report."""
        displaced, displaced_mark, displaced_protected = self._describe(name)
        current, current_mark, current_protected = self._describe(self.path.name)
        success = (
            displaced is not None
            and current is not None
            and file_identity(displaced) == held_id
            and file_identity(current) == created
            and displaced_mark
            and current_mark
            and not displaced_protected
            and not current_protected
        )
        if success and self._discard_report(name, held_id):
            return
        if success:
            displaced, displaced_mark, displaced_protected = self._describe(name)
            current, current_mark, current_protected = self._describe(self.path.name)
        ours_on_dest = (
            current is not None
            and file_identity(current) == created
            and current_mark
            and not current_protected
        )
        stranger_on_temp = (
            displaced is not None
            and file_identity(displaced) != created
            and (
                displaced_protected
                or not displaced_mark
                or file_identity(displaced) != held_id
            )
        )
        if ours_on_dest and stranger_on_temp:
            # The temporary name holds a stranger and the destination holds the
            # new report. Put the stranger on the destination, then drop ours.
            _renameat2(self.directory, name, self.directory, self.path.name, RENAME_EXCHANGE)
            raise ValueError(_ALIAS)
        if (
            displaced is not None
            and displaced_mark
            and not displaced_protected
            and (current is None or file_identity(current) != file_identity(displaced))
            and self._discard_report(name, file_identity(displaced))
            and ours_on_dest
        ):
            return
        raise ValueError(_ALIAS)

    def _describe(self, name: str):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory)
        except OSError:
            return None, False, False
        try:
            info = os.fstat(fd)
            found = _generation_fd(fd)
            return info, stat.S_ISREG(info.st_mode), _blocks_unlink(info, found, self.path)
        finally:
            os.close(fd)

    def _discard_report(self, name: str, expected: tuple[int, int]) -> bool:
        """Delete a report inode we created. Leave any other inode in place.

        The public name is checked with ``fstat`` after it is opened. A match
        is moved into a private directory and unlinked there, so a rename onto
        the public name cannot be deleted by that unlink. A stranger moved in
        that window is renamed back. When ``renameat2`` is unavailable the
        public name is unlinked only if a second ``fstat`` still matches.
        """
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory)
        except FileNotFoundError:
            return True
        try:
            info = os.fstat(fd)
            if not _same_report(info, expected, fd, self.path):
                return False
        finally:
            os.close(fd)
        parent = _directory_path(self.directory)
        private = Path(tempfile.mkdtemp(prefix=".apr-discard-", dir=parent))
        try:
            private_fd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            os.rmdir(private)
            return _unlink_if_same(self.directory, name, expected, output=self.path)
        try:
            try:
                _renameat2(self.directory, name, private_fd, "item", RENAME_NOREPLACE)
            except ValueError:
                return False
            except OSError:
                return _unlink_if_same(self.directory, name, expected, output=self.path)
            try:
                item = os.open("item", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=private_fd)
            except FileNotFoundError:
                return False
            try:
                moved = os.fstat(item)
                if _same_report(moved, expected, item, self.path):
                    os.unlink("item", dir_fd=private_fd)
                    return True
            finally:
                os.close(item)
            self._restore_stranger(private_fd, name)
            return False
        finally:
            os.close(private_fd)
            try:
                os.rmdir(private)
            except OSError:
                # The directory still holds an inode we refused to delete.
                pass

    def _restore_stranger(self, private_fd: int, name: str) -> None:
        """Put an inode we did not mean to move back on a public name."""
        try:
            _renameat2(private_fd, "item", self.directory, name, RENAME_NOREPLACE)
            return
        except (OSError, ValueError):
            pass
        preserved = f".apr-preserved-{uuid4().hex}"
        try:
            _renameat2(private_fd, "item", self.directory, preserved, RENAME_NOREPLACE)
        except (OSError, ValueError):
            return

    def _publish_fresh(self, content: str, original: tuple[int, int] | None) -> None:
        # Stage beside the destination. A temp directory on another filesystem
        # makes the publish rename fail with EXDEV after the judge has run.
        parent = _directory_path(self.directory)
        staging = Path(tempfile.mkdtemp(prefix=".apr-staging-", dir=parent))
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
            # The destination directory can be renamed while this fd is open.
            # Remove the staging inode through the fd, not the old path.
            try:
                _empty_directory(staging_fd)
            except OSError:
                pass
            os.close(staging_fd)
            try:
                os.rmdir(staging.name, dir_fd=self.directory)
            except OSError:
                shutil.rmtree(staging, ignore_errors=True)

    def _unlink_ours(self, name: str, created: tuple[int, int] | None) -> None:
        """Delete this writer's temporary file, never the destination or an input."""
        if created is None:
            return
        described, _mark, protected = self._describe(name)
        if described is None or file_identity(described) != created or protected:
            return
        try:
            dest = os.stat(self.path.name, dir_fd=self.directory, follow_symlinks=False)
        except FileNotFoundError:
            dest = None
        if dest is not None and file_identity(dest) == created:
            return
        self._discard_report(name, created)


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
        _unlink_created(directory, name, fd)
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
