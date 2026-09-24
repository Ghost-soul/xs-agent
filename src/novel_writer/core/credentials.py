import argparse
import ctypes
import getpass
import re
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Literal, Protocol

from novel_writer.core.private_files import atomic_private_write
from novel_writer.providers.base import ProviderName

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_SESSION = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NO_SUCH_LOGON_SESSION = 1312
_TARGET_PREFIX = "novel-writer"


class CredentialStore(Protocol):
    def get_api_key(self, provider: str | ProviderName) -> str | None: ...

    def set_api_key(self, provider: str | ProviderName, api_key: str) -> None: ...

    def delete_api_key(self, provider: str | ProviderName) -> None: ...


class CredentialError(RuntimeError):
    pass


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise CredentialError("Windows Credential Manager is only available on Windows")
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._cred_read = self._advapi32.CredReadW
        self._cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CredentialW)),
        ]
        self._cred_read.restype = wintypes.BOOL
        self._cred_write = self._advapi32.CredWriteW
        self._cred_write.argtypes = [
            ctypes.POINTER(_CredentialW),
            wintypes.DWORD,
        ]
        self._cred_write.restype = wintypes.BOOL
        self._cred_delete = self._advapi32.CredDeleteW
        self._cred_delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._cred_delete.restype = wintypes.BOOL
        self._cred_free = self._advapi32.CredFree
        self._cred_free.argtypes = [ctypes.c_void_p]
        self._cred_free.restype = None

    def delete_api_key(self, provider: str | ProviderName) -> None:
        if self._cred_delete(_target(provider), _CRED_TYPE_GENERIC, 0):
            return
        error = ctypes.get_last_error()
        if error != 1168:
            raise CredentialError(f"Could not delete credential (Windows error {error})")

    def get_api_key(self, provider: str | ProviderName) -> str | None:
        pointer = ctypes.POINTER(_CredentialW)()
        if not self._cred_read(
            _target(provider),
            _CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            error = ctypes.get_last_error()
            if error == 1168:
                return None
            raise CredentialError(f"Could not read credential (Windows error {error})")
        try:
            credential = pointer.contents
            raw = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            return raw.decode("utf-16-le")
        finally:
            self._cred_free(pointer)

    def set_api_key(self, provider: str | ProviderName, api_key: str) -> None:
        if not api_key:
            raise ValueError("API key must not be empty")
        encoded = api_key.encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _CredentialW()
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = _target(provider)
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "api-key"
        if self._cred_write(ctypes.byref(credential), 0):
            return

        error = ctypes.get_last_error()
        if error == _ERROR_NO_SUCH_LOGON_SESSION:
            # Service-style launches and some elevated shells do not expose a
            # machine-persistent logon session.  Session persistence remains in
            # Windows Credential Manager, survives backend restarts, and avoids
            # moving the secret into a database, file, environment, or logs.
            credential.Persist = _CRED_PERSIST_SESSION
            if self._cred_write(ctypes.byref(credential), 0):
                return
            error = ctypes.get_last_error()
        raise CredentialError(f"Could not write credential (Windows error {error})")


class MemoryCredentialStore:
    def __init__(self, values: dict[str | ProviderName, str] | None = None) -> None:
        self._values = values or {}

    def get_api_key(self, provider: str | ProviderName) -> str | None:
        return self._values.get(provider)

    def set_api_key(self, provider: str | ProviderName, api_key: str) -> None:
        self._values[provider] = api_key

    def delete_api_key(self, provider: str | ProviderName) -> None:
        self._values.pop(provider, None)


class FileCredentialStore:
    """Plaintext secrets protected by owner-only POSIX directory/file permissions.

    Persist this directory on a private volume. Each profile has its own file so
    concurrent updates to different profiles cannot erase another profile's key.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, provider: str | ProviderName) -> Path:
        value = provider.value if isinstance(provider, ProviderName) else provider
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", value):
            raise CredentialError("Invalid provider profile id")
        path = self.root / f"{value}.secret"
        if self.root.is_symlink() or path.is_symlink():
            raise CredentialError("Credential storage cannot use symbolic links")
        return path

    def get_api_key(self, provider: str | ProviderName) -> str | None:
        try:
            return self._path(provider).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as error:
            raise CredentialError("Could not read persistent credential storage") from error

    def set_api_key(self, provider: str | ProviderName, api_key: str) -> None:
        try:
            atomic_private_write(self._path(provider), api_key.encode("utf-8"))
        except OSError as error:
            raise CredentialError("Could not write persistent credential storage") from error

    def delete_api_key(self, provider: str | ProviderName) -> None:
        try:
            self._path(provider).unlink(missing_ok=True)
        except OSError as error:
            raise CredentialError("Could not delete persistent credential") from error


def create_credential_store(
    backend: Literal["auto", "windows", "file"],
    root: Path,
) -> CredentialStore:
    if backend == "windows" or (backend == "auto" and sys.platform == "win32"):
        return WindowsCredentialStore()
    return FileCredentialStore(root)


def _target(provider: str | ProviderName) -> str:
    value = provider.value if isinstance(provider, ProviderName) else provider
    return f"{_TARGET_PREFIX}/{value}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Store a provider API key securely.")
    parser.add_argument("provider", help="provider profile id, for example deepseek or openrouter")
    args = parser.parse_args()
    provider = args.provider.strip()
    if not provider:
        parser.error("provider profile id must not be empty")
    api_key = getpass.getpass(f"{provider} API key: ")
    from novel_writer.core.config import get_settings

    settings = get_settings()
    store = create_credential_store(
        settings.credential_backend,
        settings.credentials_root or settings.provider_profiles_path.parent / "credentials",
    )
    store.set_api_key(provider, api_key)
    print(f"Stored {_target(provider)} in the configured credential store.")


if __name__ == "__main__":
    main()
