import datetime
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Union

from app import lock_app
from app.attack.hashcat_cmd import HashcatCmdStdout
from app.config import WORDLISTS_USER_DIR
from app.domain import WordList, Rule, NONE_STR
from app.logger import logger
from app.utils import subprocess_call
from app.utils.file_io import calculate_md5, read_last_benchmark
from app.word_magic.digits.create_digits import read_mask


class WordListInfo:
    fast_count = 700_000

    def __init__(self, path, rate=None, count=None, url=None, checksum=None):
        self.path = path
        self.rate = rate
        self.count = count
        self.url = url
        self.checksum = checksum
        self.update_count()

    def update_count(self):
        if self.custom:
            if not self.path.exists():
                # Keep the declared/default count for installable built-in lists
                # until the file is actually present on disk.
                return
            if self.script:
                self.count = None
                return
            self.count = count_wordlist(self.path)

    @property
    def name(self):
        if self.custom:
            if self.script:
                return f"user/{self.path.name} (script)"
            return f"user/{self.path.name}"
        return self.path.name

    @property
    def custom(self) -> bool:
        return str(self.path).startswith(str(WORDLISTS_USER_DIR))

    @property
    def script(self) -> bool:
        return is_wordlist_script(self.path)

    def __str__(self):
        extra = ""
        if self.rate is not None:
            extra = f"rate={self.rate}"
        if self.url is not None and not self.path.exists():
            extra = f"{extra}; requires downloading"
        if extra:
            return f"{self.name} [{extra}]"
        return self.name

    def download(self):
        if self.path is None or self.path.exists():
            return
        if self.url is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        gzip_file = self.url.split('/')[-1]
        gzip_file = self.path.with_name(gzip_file)
        txt_path = gzip_file.parent / gzip_file.stem
        logger.debug(f"Downloading {gzip_file}")
        try:
            if calculate_md5(gzip_file) != self.checksum:
                self._download_archive(gzip_file)
            with lock_app:
                self._extract_archive(gzip_file, txt_path)
            shutil.move(txt_path, self.path)
            logger.debug(f"Downloaded and extracted {self.path}")
        except Exception:
            for partial_path in (gzip_file, txt_path, self.path):
                try:
                    if Path(partial_path).exists():
                        Path(partial_path).unlink()
                except OSError:
                    logger.warning(f"Could not clean up partial wordlist file: {partial_path}")
            raise

    def _download_archive(self, gzip_file: Path):
        if os.name != "nt":
            try:
                subprocess_call(['wget', '-q', self.url, '-O', gzip_file])
            except FileNotFoundError:
                logger.warning("wget is not available; falling back to urllib download")

        if calculate_md5(gzip_file) == self.checksum:
            return

        logger.warning(f"Primary download for {self.url} was unavailable or invalid; retrying with urllib")
        if gzip_file.exists():
            gzip_file.unlink()

        request = urllib.request.Request(
            self.url,
            headers={
                "User-Agent": "HashcatWPAServer/1.0 (+https://github.com/EpicNori/Hashcat-Pwnagotchi-server)",
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response, gzip_file.open('wb') as target_file:
            shutil.copyfileobj(response, target_file)

        downloaded_checksum = calculate_md5(gzip_file)
        if downloaded_checksum != self.checksum:
            raise RuntimeError(f"Checksum mismatch for {gzip_file.name}")

    @staticmethod
    def _extract_archive(gzip_file: Path, txt_path: Path):
        if txt_path.exists():
            txt_path.unlink()
        with gzip.open(gzip_file, 'rb') as source, txt_path.open('wb') as target_file:
            shutil.copyfileobj(source, target_file)


SCRIPT_SUFFIXES = {".sh", ".bash", ".py", ".ps1", ".cmd", ".bat"}


def is_wordlist_script(path: Path) -> bool:
    path = Path(path)
    return path.suffix.lower() in SCRIPT_SUFFIXES


@lru_cache()
def count_wordlist(wordlist_path):
    st_size_mb = Path(wordlist_path).stat().st_size / (2 ** 20)
    if st_size_mb < 150:
        wordlist_path = Path(wordlist_path)
        try:
            if os.name != "nt":
                out, err = subprocess_call(['wc', '-l', str(wordlist_path)])
                out = out.rstrip('\n')
                counter = 0
                if re.fullmatch(r"\d+\s+.*", out):
                    counter = out.split()[0]
                return int(counter)
        except Exception:
            logger.warning(f"wc line count failed for {wordlist_path}, falling back to Python counting")

        with wordlist_path.open('r', errors='ignore') as handle:
            return sum(1 for _ in handle)
    count_per_mb = 100510.62068189554  # from top109M
    count_approx = int(st_size_mb * count_per_mb)
    return count_approx


class WordListDefault:
    TOP109M = WordListInfo(
        path=WORDLISTS_USER_DIR / WordList.TOP109M.value,
        rate=39,
        count=109_438_614,
        url="https://download.weakpass.com/wordlists/1852/Top109Million-probable-v2.txt.gz",
        checksum="c0a26fd763d56a753a5f62c517796d09"
    )
    TOP29M = WordListInfo(
        path=WORDLISTS_USER_DIR / WordList.TOP29M.value,
        rate=30,
        count=29_040_646,
        url="https://download.weakpass.com/wordlists/1857/Top29Million-probable-v2.txt.gz",
        checksum="807ee2cf835660b474b6fd15bca962cf"
    )
    TOP1M = WordListInfo(
        path=WORDLISTS_USER_DIR / WordList.TOP1M.value,
        rate=19,
        count=1_667_462,
        url="https://download.weakpass.com/wordlists/1855/Top1pt6Million-probable-v2.txt.gz",
        checksum="2d45c4aa9f4a87ece9ebcbd542613f50"
    )
    TOP304K = WordListInfo(
        path=WORDLISTS_USER_DIR / WordList.TOP304K.value,
        rate=12,
        count=303_872,
        url="https://download.weakpass.com/wordlists/1859/Top304Thousand-probable-v2.txt.gz",
        checksum="f99e6a581597cbdc76efc1bcc001a9ed"
    )

    @staticmethod
    def list():
        return [WordListDefault.TOP109M, WordListDefault.TOP29M,
                WordListDefault.TOP1M, WordListDefault.TOP304K]

    @staticmethod
    def get(path):
        d = {}
        for wlist in WordListDefault.list():
            d[str(wlist.path)] = wlist
        return d.get(str(path))


def download_wordlist(wordlist_path: Path):
    wordlist = find_wordlist_by_path(wordlist_path)
    if wordlist is None:
        # fast mode or does not exist
        return
    wordlist.download()


@lru_cache()
def count_rules(rule: Rule):
    # counts the multiplier
    if rule is None:
        return 1
    rules = read_mask(rule.path)
    return len(rules)


def estimate_runtime_fmt(wordlist_path: Path, rule: Rule) -> str:
    speed = int(read_last_benchmark().speed)
    if speed == 0:
        return "unknown"

    n_words = 0
    if wordlist_path == NONE_STR:
        wordlist_path = None
    if wordlist_path is not None:
        wordlist = find_wordlist_by_path(wordlist_path)
        if wordlist is None:
            return "unknown"
        if wordlist.script:
            return "unknown"
        n_words += wordlist.count

    n_candidates = n_words * count_rules(rule)

    # add extra words to account for the 'fast' run, which includes
    # 160k digits8, 120k top1k+best64 and ESSID manipulation
    # (300k hamming ball, 70k digits append mask)
    n_candidates += WordListInfo.fast_count

    runtime = int(n_candidates / speed)  # in seconds
    runtime_ftm = str(datetime.timedelta(seconds=runtime))
    return runtime_ftm


def create_fast_wordlists():
    # note that dumping all combinations in a file is not equivalent to
    # directly adding top1k wordlist and best64 rule because hashcat ignores
    # patterns that are <8 chars _before_ expanding a candidate with the rule.
    if not WordList.TOP1K_RULE_BEST64.path.exists():
        # it should be already created in a docker
        logger.warning(f"{WordList.TOP1K_RULE_BEST64.name} does not exist. Creating")
        top1k_url = "https://download.weakpass.com/wordlists/1854/Top1575-probable2.txt.gz"
        wlist_top1k = WordListInfo(path=WordList.TOP1K.path, url=top1k_url,
                                   checksum="070a10f5e7a23f12ec6fc8c8c0ccafe8")
        wlist_top1k.download()
        hashcat_stdout = HashcatCmdStdout(outfile=WordList.TOP1K_RULE_BEST64.path)
        hashcat_stdout.add_wordlists(WordList.TOP1K)
        hashcat_stdout.add_rule(Rule.BEST_64)
        subprocess_call(hashcat_stdout.build())
        with open(WordList.TOP1K_RULE_BEST64.path) as f:
            unique = set(f.readlines())
        with open(WordList.TOP1K_RULE_BEST64.path, 'w') as f:
            f.writelines(unique)


def find_wordlist_by_path(wordlist_path) -> Union[WordListInfo, None]:
    if wordlist_path is None:
        return None
    wlist = WordListDefault.get(wordlist_path)
    if wlist is None:
        # user wordlist
        return WordListInfo(wordlist_path)
    return deepcopy(wlist)


def find_wordlist_by_name(wordlist_name) -> Union[WordListInfo, None]:
    if wordlist_name in (None, NONE_STR):
        return None

    for wlist in WordListDefault.list():
        if wordlist_name in (wlist.name, wlist.path.name):
            return deepcopy(wlist)

    if str(wordlist_name).startswith("user/"):
        custom_path = WORDLISTS_USER_DIR / str(wordlist_name).split("/", 1)[1]
        if custom_path.exists():
            return WordListInfo(custom_path)

    for custom_path in sorted(WORDLISTS_USER_DIR.iterdir()):
        custom_wlist = WordListInfo(path=custom_path)
        if wordlist_name in (custom_wlist.name, custom_path.name):
            return custom_wlist

    return None


def wordlist_choices():
    wlists_info = WordListDefault.list()
    for custom_path in sorted(WORDLISTS_USER_DIR.iterdir()):
        wlists_info.append(WordListInfo(path=custom_path))

    choices = [(NONE_STR, "(fast)")]
    choices.extend((str(wlist.path), str(wlist)) for wlist in wlists_info)

    return choices

def materialize_wordlist_source(wordlist_path: Path) -> Path:
    """
    Convert a user-provided wordlist source into a real text file path.

    Plain text wordlists are returned as-is. Supported scripts are executed and
    their stdout is written to a temporary wordlist file for the current job.
    """
    wordlist_path = Path(wordlist_path)
    if not is_wordlist_script(wordlist_path):
        return wordlist_path

    if not wordlist_path.exists():
        raise FileNotFoundError(f"Wordlist script not found: {wordlist_path}")

    suffix = wordlist_path.suffix.lower()
    if suffix == ".py":
        command = [sys.executable, str(wordlist_path)]
    elif suffix == ".ps1":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wordlist_path)
        ]
    elif suffix in {".cmd", ".bat"}:
        command = ["cmd.exe", "/c", str(wordlist_path)]
    else:
        shell = "bash" if os.name == "nt" else "/bin/bash"
        command = [shell, str(wordlist_path)]

    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
        cwd=str(WORDLISTS_USER_DIR)
    )
    if completed.returncode != 0:
        error_output = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Wordlist script failed: {error_output or wordlist_path.name}")

    generated = tempfile.NamedTemporaryFile(
        mode="w",
        prefix=f"generated_{wordlist_path.stem}_",
        suffix=".txt",
        delete=False
    )
    with generated:
        generated.write(completed.stdout)

    return Path(generated.name)


def iter_user_wordlist_scripts():
    for custom_path in sorted(WORDLISTS_USER_DIR.iterdir()):
        if custom_path.is_file() and is_wordlist_script(custom_path):
            yield custom_path


def cyrrilic2qwerty(wlist: WordList):
    txt_cyrrilic = wlist.path.read_text().lower()
    ru = "йцукенгшщзхъфывапролджэячсмитьбю."
    en = "qwertyuiop[]asdfghjkl;'zxcvbnm,./"
    table = txt_cyrrilic.maketrans(ru, en)
    txt_qwerty = txt_cyrrilic.translate(table)
    return txt_qwerty


if __name__ == '__main__':
    create_fast_wordlists()
