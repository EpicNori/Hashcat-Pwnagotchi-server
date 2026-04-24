import os
from pathlib import Path
import re
import shutil

from app.logger import logger
from app.domain import InvalidFileError
from app.utils import subprocess_call, check_file_22000, calculate_md5
from app.utils.file_io import parse_wpa_hash_line


def _safe_group_filename(bssid: str, essid_hex: str, index: int, suffix: str) -> str:
    safe_bssid = re.sub(r'[^0-9A-Fa-f]', '', bssid) or f"bssid{index}"
    safe_essid = re.sub(r'[^0-9A-Fa-f]', '', essid_hex) or f"essid{index}"
    return f"{index:03d}_{safe_bssid}_{safe_essid}{suffix}"


def _split_by_essid_fallback(file_22000: Path, to_folder: Path, output_suffix: str):
    groups = {}
    with file_22000.open('r', errors='ignore') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            bssid, essid_hex = parse_wpa_hash_line(line)
            groups.setdefault((bssid, essid_hex), []).append(line)

    if not groups:
        raise InvalidFileError("No valid hashes found in supported WPA hash file")

    for index, ((bssid, essid_hex), lines) in enumerate(groups.items(), start=1):
        output_path = to_folder / _safe_group_filename(bssid, essid_hex, index, output_suffix)
        output_path.write_text('\n'.join(lines) + '\n')


def _windows_path_to_wsl(path: Path) -> str:
    path = Path(path).resolve()
    drive = path.drive.rstrip(':').lower()
    rest = path.as_posix().split(':', 1)[-1]
    return f"/mnt/{drive}{rest}"


def _quote_bash(arg: str) -> str:
    return "'" + arg.replace("'", "'\"'\"'") + "'"


def run_hcx_command(args, working_directory: Path | None = None):
    try:
        return subprocess_call(args)
    except FileNotFoundError as e:
        if os.name != "nt" or not shutil.which("wsl.exe"):
            executable = args[0] if args else "unknown"
            raise FileNotFoundError(
                f"Missing dependency: '{executable}'. Please install 'hcxtools' and 'hashcat'."
            ) from e

        distro = os.environ.get("HASHCAT_WPA_WSL_DISTRO", "Ubuntu")
        translated_args = []
        for arg in args:
            text = str(arg)
            if re.match(r"^[A-Za-z]:[\\/]", text):
                translated_args.append(_windows_path_to_wsl(Path(text)))
            else:
                translated_args.append(text)

        if working_directory is not None:
            wsl_cwd = _windows_path_to_wsl(Path(working_directory))
            bash_cmd = f"cd {_quote_bash(wsl_cwd)} && {' '.join(_quote_bash(arg) for arg in translated_args)}"
            return subprocess_call(["wsl.exe", "-d", distro, "--", "bash", "-lc", bash_cmd])

        return subprocess_call(["wsl.exe", "-d", distro, "--", *translated_args])


def convert_to_22000(capture_path):
    """
    Convert airodump `.cap` to hashcat `.22000`
    """
    file_22000 = Path(capture_path).with_suffix(".22000")

    def convert_and_verify(cmd):
        out, err = run_hcx_command(cmd)
        if not Path(file_22000).exists() or Path(file_22000).stat().st_size == 0:
            error_msg = err.strip().splitlines()[0] if err.strip() else "No valid handshakes found in capture"
            raise InvalidFileError(f"Conversion failed: {error_msg}")

    if re.fullmatch(r"\.(p?cap|pcapng)", capture_path.suffix, flags=re.IGNORECASE):
        convert_and_verify(['hcxpcapngtool', '-o', str(file_22000), str(capture_path)])
        capture_path = file_22000

    if capture_path.suffix in (".2500", ".2501", ".16800", ".16801", ".22000", ".22001"):
        # Already in a supported text format; keep it as-is so the hash mode suffix
        # stays aligned with the later hashcat invocation.
        return Path(capture_path)
    if capture_path.suffix == ".hccapx":
        convert_and_verify(['hcxmactool', f'--hccapxin={capture_path}', f'--pmkideapolout={file_22000}'])
    elif capture_path.suffix == ".pmkid":
        convert_and_verify(['hcxmactool', f'--pmkidin={capture_path}', f'--pmkideapolout={file_22000}'])
    elif capture_path.suffix != ".22000":
        raise InvalidFileError(f"Invalid file suffix: '{capture_path.suffix}'")

    return file_22000


def split_by_essid(file_22000, to_folder=None):
    file_22000 = Path(file_22000)
    check_file_22000(file_22000)
    if to_folder is None:
        checksum = calculate_md5(file_22000)
        to_folder = Path(f"{file_22000.with_suffix('')}_{checksum}")
        if to_folder.exists():
            # should never happen
            logger.warning(f"{to_folder} already exists")
    to_folder.mkdir(exist_ok=True)
    curdir = os.getcwd()
    output_suffix = file_22000.suffix
    used_external_split = False
    try:
        os.chdir(to_folder)
        if output_suffix == ".22000":
            run_hcx_command(['hcxhashtool', '-i', file_22000, '--essid-group'], working_directory=to_folder)
            used_external_split = any(
                partial.is_file() and partial.suffix == output_suffix
                for partial in to_folder.iterdir()
            )
    except FileNotFoundError:
        logger.warning("hcxhashtool is not available; falling back to built-in ESSID splitting")
    finally:
        os.chdir(curdir)

    if not used_external_split:
        for partial in to_folder.iterdir():
            if partial.is_file():
                partial.unlink()
        _split_by_essid_fallback(file_22000, to_folder, output_suffix=output_suffix)

    return to_folder
