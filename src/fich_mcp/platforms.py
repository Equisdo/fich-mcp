"""OS boundaries: private storage, nonblocking locks and child containment.

Windows uses pywin32 only on Windows. Never fall back to an unlocked file or
pretend chmod protects credentials on NTFS. Storage must be on a local disk.
"""

import os
import stat
from contextlib import contextmanager


def reject_link(path):
    from .security import FichError

    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise FichError("unsafe_storage")


def _windows_identity():
    import win32api
    import win32con
    import win32security

    with win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY) as token:
        return win32security.GetTokenInformation(token, win32security.TokenUser)[0]


def windows_private(path, *, repair=False):
    """Owner-only protected DACL; reject null/broad ACLs when reading secrets."""
    import win32security as security

    from .security import FichError

    reject_link(path)
    sid = _windows_identity()
    info = security.GetNamedSecurityInfo(
        str(path), security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION,
    )
    if info.GetSecurityDescriptorOwner() != sid:
        raise FichError("unsafe_storage")
    if repair:
        acl = security.ACL()
        acl.AddAccessAllowedAceEx(
            security.ACL_REVISION,
            security.OBJECT_INHERIT_ACE | security.CONTAINER_INHERIT_ACE if path.is_dir() else 0,
            0x1F01FF, sid,
        )
        security.SetNamedSecurityInfo(
            str(path), security.SE_FILE_OBJECT,
            security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, acl, None,
        )
        return
    acl = info.GetSecurityDescriptorDacl()
    if acl is None or acl.GetAceCount() == 0:
        raise FichError("unsafe_storage")
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if ace[0][0] != security.ACCESS_ALLOWED_ACE_TYPE or ace[2] != sid:
            raise FichError("unsafe_storage")


@contextmanager
def exclusive_writer(path):
    """Keep the sidecar inode, fail immediately on contention, release on close/death."""
    from .security import FichError

    reject_link(path)
    if os.name == "nt":
        import win32con
        import win32file
        import pywintypes

        try:
            handle = win32file.CreateFile(
                str(path), win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0, None, win32con.OPEN_ALWAYS,
                win32con.FILE_FLAG_OPEN_REPARSE_POINT, None,
            )
        except pywintypes.error as exc:
            if exc.winerror == 32:  # ERROR_SHARING_VIOLATION, not an ACL error
                raise FichError("sync_busy") from None
            raise
        try:
            if win32file.GetFileInformationByHandle(handle)[0] & 0x400:
                raise FichError("unsafe_storage")
            yield
        finally:
            handle.Close()
    else:
        import fcntl

        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise FichError("sync_busy") from None
            yield
        finally:
            os.close(fd)


def windows_job(pid=None, *, pdf=False):
    """Kill descendants when closed; optionally bound PDF memory and CPU.

    The RPC child waits for stdin before work, so its parent assigns it before
    sending a request. PDF children assign themselves before importing pypdf.
    """
    import win32api
    import win32job

    job = win32job.CreateJobObject(None, None)
    try:
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        basic = info["BasicLimitInformation"]
        basic["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if pdf:
            basic["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY | win32job.JOB_OBJECT_LIMIT_PROCESS_TIME
            )
            info["ProcessMemoryLimit"] = 768 * 1024**2
            basic["PerProcessUserTimeLimit"] = 12 * 10_000_000
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        if pid is None:
            win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
        else:
            with win32api.OpenProcess(0x0100 | 0x0001, False, pid) as process:
                win32job.AssignProcessToJobObject(job, process)
        return job
    except BaseException:
        job.Close()
        raise
