import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,253}$")
DEFAULT_TIMEOUT = 20
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


@dataclass
class SshResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_status: int | None = None
    message: str = ""


def _load_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError as error:
        raise RuntimeError(
            "Paramiko is not installed. Run `pip install -r requirements.txt` on the server."
        ) from error


def validate_host(host: str) -> str:
    host = (host or "").strip()
    if not host or not HOST_RE.fullmatch(host):
        raise ValueError("Use a hostname or IP address without spaces.")
    return host


def validate_remote_path(remote_path: str) -> str:
    remote_path = (remote_path or "").strip()
    if not remote_path or "\x00" in remote_path:
        raise ValueError("Remote path is required.")
    if len(remote_path) > 512:
        raise ValueError("Remote path is too long.")
    is_directory = remote_path.endswith("/")
    normalized = str(PurePosixPath(remote_path))
    return f"{normalized}/" if is_directory and not normalized.endswith("/") else normalized


def connect(host: str, username: str, password: str, port: int = 22, timeout: int = DEFAULT_TIMEOUT):
    paramiko = _load_paramiko()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        validate_host(host),
        port=port,
        username=(username or "").strip(),
        password=password or "",
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def run_command(host: str, username: str, password: str, command: str, port: int = 22) -> SshResult:
    command = (command or "").strip()
    if not command:
        raise ValueError("Command is required.")
    if len(command) > 2000:
        raise ValueError("Command is too long.")

    client = connect(host, username, password, port=port)
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=DEFAULT_TIMEOUT)
        stdin.close()
        exit_status = stdout.channel.recv_exit_status()
        stdout_text = stdout.read().decode("utf-8", errors="replace")
        stderr_text = stderr.read().decode("utf-8", errors="replace")
        return SshResult(
            ok=exit_status == 0,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_status=exit_status,
            message="Command completed." if exit_status == 0 else "Command failed.",
        )
    finally:
        client.close()


def upload_file(host: str, username: str, password: str, file_storage: FileStorage, remote_path: str,
                port: int = 22) -> SshResult:
    if not file_storage or not file_storage.filename:
        raise ValueError("Choose a file to upload.")

    filename = secure_filename(file_storage.filename)
    if not filename:
        raise ValueError("Uploaded filename is not valid.")

    remote_path = validate_remote_path(remote_path)
    if remote_path.endswith("/"):
        remote_path = posixpath.join(remote_path, filename)

    stream = file_storage.stream
    current = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(current)
    if size > MAX_UPLOAD_BYTES:
        raise ValueError("Upload is larger than 32 MB.")

    client = connect(host, username, password, port=port)
    try:
        sftp = client.open_sftp()
        try:
            with sftp.file(remote_path, "wb") as remote_file:
                remote_file.write(file_storage.read())
        finally:
            sftp.close()
        return SshResult(ok=True, message=f"Uploaded {filename} to {remote_path}.")
    finally:
        client.close()
