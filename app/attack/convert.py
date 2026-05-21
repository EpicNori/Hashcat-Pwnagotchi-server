from pathlib import Path
import re

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


def run_hcx_command(args, working_directory: Path | None = None):
    try:
        return subprocess_call(args, cwd=str(working_directory) if working_directory is not None else None)
    except FileNotFoundError as e:
        executable = args[0] if args else "unknown"
        raise FileNotFoundError(
            f"Missing dependency: '{executable}'. Please install 'hcxtools' and 'hashcat'."
        ) from e


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
    output_suffix = file_22000.suffix
    used_external_split = False
    try:
        if output_suffix == ".22000":
            # Pass working_directory so subprocess runs in the right folder without chdir()
            # We must use str(file_22000.resolve()) so hcxhashtool can find it from the new cwd
            file_absolute = str(file_22000.resolve())
            run_hcx_command(['hcxhashtool', '-i', file_absolute, '--essid-group'], working_directory=to_folder)
            used_external_split = any(
                partial.is_file() and partial.suffix == output_suffix
                for partial in to_folder.iterdir()
            )
    except FileNotFoundError:
        logger.warning("hcxhashtool is not available; falling back to built-in ESSID splitting")

    if not used_external_split:
        for partial in to_folder.iterdir():
            if partial.is_file():
                partial.unlink()
        _split_by_essid_fallback(file_22000, to_folder, output_suffix=output_suffix)

    return to_folder
