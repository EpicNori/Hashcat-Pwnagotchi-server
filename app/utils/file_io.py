import hashlib
import os
import secrets
from pathlib import Path, PurePosixPath

from app import lock_app
from app.config import BENCHMARK_FILE, HASHCAT_BRAIN_PASSWORD_PATH, HASHCAT_WPA_CACHE_DIR, WORDLISTS_USER_DIR
from app.domain import Benchmark, InvalidFileError, HashcatMode
from app.logger import logger

CAPTURES_DIR = HASHCAT_WPA_CACHE_DIR / "captures"
SUPPORTED_HASH_SUFFIXES = set(HashcatMode.valid_modes())


def iter_capture_roots():
    roots = []

    def add_root(path_candidate):
        if not path_candidate:
            return
        path_obj = Path(path_candidate).expanduser()
        if path_obj not in roots:
            roots.append(path_obj)

    add_root(CAPTURES_DIR)
    add_root(Path.home() / ".hashcat" / "wpa-server" / "captures")

    install_root = os.environ.get("HASHCAT_WPA_INSTALL_ROOT")
    if install_root:
        install_root_path = Path(install_root)
        add_root(install_root_path / "data" / "captures")
        add_root(install_root_path / "captures")
        add_root(install_root_path / "current" / "captures")

    add_root("/var/lib/hashcat-wpa-server/captures")

    return tuple(roots)


def normalize_stored_capture_filename(saved_filename: str) -> str:
    raw_filename = str(saved_filename).strip()
    normalized = raw_filename.replace('\\', '/')

    try:
        absolute_path = Path(raw_filename).expanduser().resolve(strict=False)
    except OSError:
        absolute_path = None

    if absolute_path and absolute_path.is_absolute():
        for capture_root in iter_capture_roots():
            try:
                relative_path = absolute_path.relative_to(capture_root.resolve(strict=False))
                return PurePosixPath(*relative_path.parts).as_posix()
            except ValueError:
                continue

    return PurePosixPath(normalized).as_posix()


def resolve_existing_capture_path(saved_filename: str) -> Path:
    normalized_filename = normalize_stored_capture_filename(saved_filename)
    relative_filename = PurePosixPath(normalized_filename)
    candidate_paths = []

    raw_path = Path(str(saved_filename)).expanduser()
    if raw_path.is_absolute():
        candidate_paths.append(raw_path)

    for capture_root in iter_capture_roots():
        primary_path = capture_root.joinpath(*relative_filename.parts)
        candidate_paths.append(primary_path)
        if relative_filename.name and primary_path != capture_root / relative_filename.name:
            candidate_paths.append(capture_root / relative_filename.name)

    seen = set()
    for candidate in candidate_paths:
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate.exists():
            return candidate

    if relative_filename.name:
        for capture_root in iter_capture_roots():
            if not capture_root.exists():
                continue
            matches = sorted(capture_root.rglob(relative_filename.name), key=lambda path: len(path.parts))
            for match in matches:
                try:
                    match.relative_to(capture_root)
                    return match
                except ValueError:
                    continue

    fallback = CAPTURES_DIR.joinpath(*relative_filename.parts)
    try:
        fallback.relative_to(CAPTURES_DIR)
        return fallback
    except ValueError:
        raise PermissionError("Resolved path escapes capture directory")


def parse_wpa_hash_line(line: str):
    parts = line.strip().split('*')
    if not parts or "WPA" not in parts:
        raise InvalidFileError("Not a supported WPA hash line")

    wpa_index = parts.index("WPA")
    if len(parts) <= wpa_index + 5:
        raise InvalidFileError("Not a supported WPA hash line")

    return parts[wpa_index + 3], parts[wpa_index + 5]


def decode_essid_hex(essid_hex: str) -> str:
    """Decode an ESSID stored as hex without crashing on non-UTF-8 bytes."""
    raw_essid = bytes.fromhex(essid_hex)
    try:
        return raw_essid.decode("utf-8")
    except UnicodeDecodeError:
        return raw_essid.decode("utf-8", errors="replace")


def read_plain_key(key_path):
    key_path = Path(key_path)
    if not key_path.exists():
        return None
    try:
        with open(key_path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return None
    found_keys = []
    seen_keys = set()
    for line in lines:
        key_parts = line.split(':', 1)
        if len(key_parts) != 2:
            continue
        hash_value, key = key_parts
        if not hash_value or not key:
            continue
        found_key = "{hash_value}:{key}".format(hash_value=hash_value, key=key)
        if found_key in seen_keys:
            continue
        seen_keys.add(found_key)
        found_keys.append(found_key)
    if not found_keys:
        return None
    return ', '.join(found_keys)


def read_last_benchmark():
    if not BENCHMARK_FILE.exists():
        return Benchmark(date="(Never)", speed=0)
    with lock_app, open(BENCHMARK_FILE) as f:
        lines = f.read().splitlines()
    if not lines:
        return Benchmark(date="(Never)", speed=0)
    last_line = lines[-1]
    parts = last_line.split(',', 1)
    if len(parts) != 2:
        logger.warning("Benchmark file has a corrupt line: %r", last_line)
        return Benchmark(date="(corrupt)", speed=0)
    date_str, speed = parts
    return Benchmark(date=date_str, speed=speed)


def read_hashcat_brain_password():
    if not HASHCAT_BRAIN_PASSWORD_PATH.exists():
        logger.error("Hashcat brain password file does not exist. Generating a random password.")
        HASHCAT_BRAIN_PASSWORD_PATH.write_text(secrets.token_hex(16))
    with open(HASHCAT_BRAIN_PASSWORD_PATH) as f:
        brain_password = f.readline().rstrip()
    return brain_password


def bssid_essid_from_22000(file_22000):
    if not Path(file_22000).exists():
        raise FileNotFoundError(file_22000)
    with open(file_22000) as f:
        lines = f.read().splitlines()
    bssid_essids = set()
    for line in lines:
        if not line.strip():
            continue
        bssid, essid = parse_wpa_hash_line(line)
        bssid_essids.add(f"{bssid}:{essid}")
    return iter(bssid_essids)


def check_file_22000(file_22000):
    file_22000 = Path(file_22000)
    suffix = file_22000.suffix.lstrip(".")
    if suffix not in SUPPORTED_HASH_SUFFIXES:
        expected = ", ".join(f".{item}" for item in sorted(SUPPORTED_HASH_SUFFIXES))
        raise InvalidFileError(f"Invalid capture file format: '{file_22000.suffix}'. Expected one of: {expected}.")


def calculate_md5(fpath, chunk_size=1024 * 1024):
    if not Path(fpath).exists():
        return None
    md5 = hashlib.md5()
    with open(fpath, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            md5.update(chunk)
    return md5.hexdigest()


def extract_password_from_found_key(found_key):
    if not found_key:
        return None
    return str(found_key).split(':', 1)[-1].strip() or None


def build_rainbow_wordlist():
    from app.uploader import UploadedTask

    rainbow_wordlist = WORDLISTS_USER_DIR / "rainbow_processed.txt"
    seen_passwords = set()
    ordered_passwords = []

    tasks = UploadedTask.query.filter(UploadedTask.found_key.is_not(None)) \
        .order_by(UploadedTask.uploaded_time.desc()).all()
    for task in tasks:
        password = extract_password_from_found_key(task.found_key)
        if password and password not in seen_passwords:
            seen_passwords.add(password)
            ordered_passwords.append(password)

    if CAPTURES_DIR.exists():
        candidate_key_files = []
        for key_file in CAPTURES_DIR.rglob("*.key"):
            try:
                candidate_key_files.append((key_file.stat().st_mtime, key_file))
            except FileNotFoundError:
                logger.warning(f"Skipping missing rainbow cache file: {key_file}")
            except OSError as error:
                logger.warning(f"Skipping unreadable rainbow cache file {key_file}: {error}")

        for _, key_file in sorted(candidate_key_files, key=lambda item: item[0], reverse=True):
            try:
                found_key = read_plain_key(key_file)
            except Exception:
                continue
            password = extract_password_from_found_key(found_key)
            if password and password not in seen_passwords:
                seen_passwords.add(password)
                ordered_passwords.append(password)

    if not ordered_passwords:
        return None

    rainbow_wordlist.parent.mkdir(parents=True, exist_ok=True)
    try:
        rainbow_wordlist.write_text('\n'.join(ordered_passwords) + '\n')
    except OSError as error:
        logger.warning(f"Could not write rainbow reuse list {rainbow_wordlist}: {error}")
        return None

    return rainbow_wordlist


def _rainbow_cache_key(essid: str, source_wordlist: Path):
    try:
        stat = source_wordlist.stat()
        fingerprint = f"{source_wordlist.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    except OSError:
        fingerprint = str(source_wordlist)
    raw_key = f"{essid}\0{fingerprint}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw_key).hexdigest()[:24]


def build_pmk_rainbow_cache(essid: str, source_wordlist: Path):
    """
    Build an ESSID-specific WPA PMK cache for hashcat mode 22001.

    WPA uses the ESSID as PBKDF2 salt, so a real rainbow cache is only reusable
    for captures from the same network name.
    """
    source_wordlist = Path(source_wordlist)
    if not essid or not source_wordlist.exists():
        return None, None

    cache_key = _rainbow_cache_key(essid, source_wordlist)
    cache_dir = WORDLISTS_USER_DIR / "rainbow_pmk" / cache_key
    pmk_path = cache_dir / "pmk_candidates.22001"
    map_path = cache_dir / "pmk_to_password.tsv"
    meta_path = cache_dir / "meta.txt"

    if pmk_path.exists() and map_path.exists():
        return pmk_path, map_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    essid_bytes = essid.encode("utf-8", errors="replace")
    seen_pmks = set()
    written = 0

    tmp_pmk_path = pmk_path.with_suffix(".tmp")
    tmp_map_path = map_path.with_suffix(".tmp")
    with source_wordlist.open("r", encoding="utf-8", errors="ignore") as src, \
            tmp_pmk_path.open("w", encoding="ascii") as pmk_out, \
            tmp_map_path.open("w", encoding="utf-8") as map_out:
        for raw_line in src:
            password = raw_line.rstrip("\r\n")
            if not password:
                continue
            password_bytes = password.encode("utf-8", errors="ignore")
            if len(password_bytes) < 8 or len(password_bytes) > 63:
                continue
            pmk = hashlib.pbkdf2_hmac("sha1", password_bytes, essid_bytes, 4096, 32).hex()
            if pmk in seen_pmks:
                continue
            seen_pmks.add(pmk)
            pmk_out.write(pmk + "\n")
            map_out.write(f"{pmk}\t{password}\n")
            written += 1

    if written == 0:
        tmp_pmk_path.unlink(missing_ok=True)
        tmp_map_path.unlink(missing_ok=True)
        return None, None

    os.replace(tmp_pmk_path, pmk_path)
    os.replace(tmp_map_path, map_path)
    meta_path.write_text(f"essid={essid}\nsource={source_wordlist}\nentries={written}\n", encoding="utf-8")
    return pmk_path, map_path


def resolve_pmk_rainbow_password(found_key: str, map_path: Path):
    if not found_key or not map_path or not Path(map_path).exists():
        return None
    found_pmk = extract_password_from_found_key(found_key)
    if not found_pmk:
        return None
    found_pmk = found_pmk.lower()
    with Path(map_path).open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            pmk, _, password = line.rstrip("\n").partition("\t")
            if pmk.lower() == found_pmk:
                return password or None
    return None
