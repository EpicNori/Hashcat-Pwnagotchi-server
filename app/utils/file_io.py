import hashlib
import secrets
from pathlib import Path

from app import lock_app
from app.config import BENCHMARK_FILE, HASHCAT_BRAIN_PASSWORD_PATH, HASHCAT_WPA_CACHE_DIR, WORDLISTS_USER_DIR
from app.domain import Benchmark, InvalidFileError
from app.logger import logger

CAPTURES_DIR = HASHCAT_WPA_CACHE_DIR / "captures"


def read_plain_key(key_path):
    key_path = Path(key_path)
    if not key_path.exists():
        return None
    with open(key_path) as f:
        lines = f.read().splitlines()
    found_keys = set()
    for line in lines:
        essid, key = line.split(':')[-2:]
        found_keys.add("{essid}:{key}".format(essid=essid, key=key))
    if not found_keys:
        return None
    return ', '.join(found_keys)


def read_last_benchmark():
    if not BENCHMARK_FILE.exists():
        return Benchmark(date="(Never)", speed=0)
    with lock_app, open(BENCHMARK_FILE) as f:
        last_line = f.read().splitlines()[-1]
    date_str, speed = last_line.split(',')
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
        info_split = line.split('*')
        if len(info_split) == 0:
            raise InvalidFileError("Not a 22000 file")
        bssid = info_split[3]
        essid = info_split[5]  # in hex format
        bssid_essids.add(f"{bssid}:{essid}")
    return iter(bssid_essids)


def check_file_22000(file_22000):
    file_22000 = Path(file_22000)
    if file_22000.suffix != ".22000":
        raise InvalidFileError(f"Invalid capture file format: '{file_22000.suffix}'. Expected 22000.")


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
    return str(found_key).rsplit(':', 1)[-1].strip() or None


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
        for key_file in sorted(CAPTURES_DIR.rglob("*.key"), key=lambda path: path.stat().st_mtime, reverse=True):
            try:
                found_key = read_plain_key(key_file)
            except Exception:
                continue
            password = extract_password_from_found_key(found_key)
            if password and password not in seen_passwords:
                seen_passwords.add(password)
                ordered_passwords.append(password)

    rainbow_wordlist.write_text('\n'.join(ordered_passwords) + ('\n' if ordered_passwords else ''))
    return rainbow_wordlist if ordered_passwords else None
