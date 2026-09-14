"""Lifetime OS advisory lock for one Herald process per canonical database.

Acquire before database migration or interrupted-claim recovery. Keep the lease
until delivery tasks have been cancelled/awaited and the Discord client closes.
The lock file stays in place: unlinking it would allow two different inodes to
be locked simultaneously. A process crash releases the OS lock automatically.

Use a local filesystem with working advisory locks. POSIX enforces mode 0600;
Windows operators must restrict the runtime directory with its native ACLs.
"""
import errno
import os
import stat
from pathlib import Path


class InstanceAlreadyRunningError(RuntimeError):
    """Another process already holds this database's lifetime lease."""


class InstanceLease:
    def __init__(self, path: Path, descriptor: int):
        self.path = path
        self._descriptor = descriptor

    def close(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            # Closing the lifetime handle releases flock/msvcrt's byte lock.
            os.close(descriptor)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def acquire_instance(db_path: str | Path) -> InstanceLease:
    """Acquire without waiting; relative paths resolve from the caller's cwd.

    Pass storage's resolved database path when its configuration is app-relative.
    Only this lock file is opened; acquiring does not initialise the database.
    """
    database = Path(db_path).expanduser().resolve()
    lock_path = Path(str(database) + ".instance.lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    elif lock_path.is_symlink():
        raise RuntimeError("Herald instance lock must not be a symbolic link")
    descriptor = None
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("Herald instance lock must be a regular file with one link")
        os.set_inheritable(descriptor, False)
        if os.name == "posix":
            import fcntl
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif os.name == "nt":
            import msvcrt
            # Lock a real byte at offset zero. Existing leases already have it;
            # a concurrent creator writing it can fail closed under Windows.
            if info.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            raise RuntimeError("This platform has no supported Herald instance lock")
        lease = InstanceLease(lock_path, descriptor)
        descriptor = None
        return lease
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
            raise InstanceAlreadyRunningError(
                "Herald instance lock is held or access is denied; do not run recovery"
            ) from exc
        raise RuntimeError("Could not acquire the Herald instance lock") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
