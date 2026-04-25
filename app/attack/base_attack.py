import argparse
import shutil
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Union

from tqdm import tqdm

from app.attack.convert import split_by_essid
from app.attack.hashcat_cmd import HashcatCmdCapture, HashcatCmdStdout
from app.config import ESSID_TRIED
from app.domain import Rule, WordList
from app.logger import logger
from app.utils import read_plain_key, subprocess_call, bssid_essid_from_22000, \
    check_file_22000, decode_essid_hex
from app.word_magic import create_digits_wordlist, create_fast_wordlists
from app.word_magic.essid import run_essid_attack
from app.word_magic.wordlist import WordListDefault


def monitor_timer(func):
    def wrapped(*args, **kwargs):
        start = time.time()
        res = func(*args, **kwargs)
        elapsed_sec = time.time() - start
        timer = BaseAttack.timers[func.__name__]
        timer['count'] += 1
        timer['elapsed'] += elapsed_sec
        return res

    return wrapped


def download_wordlists():
    for wlist in WordListDefault.list():
        wlist.download()
    create_digits_wordlist()
    create_fast_wordlists()


class BaseAttack:
    timers = defaultdict(lambda: dict(count=0, elapsed=1e-6))
    WPA_EXHAUSTIVE_MASK = "?a" * 63

    def __init__(self, file_22000: Union[str, Path], hashcat_args=(), fast=False, verbose=True):
        """
        :param file_22000: .22000 hashcat capture file path
        :param fast: ESSID+digits fast or long attack
        :param verbose: show (True) or hide (False) tqdm
        """
        check_file_22000(file_22000)
        self.file_22000 = Path(file_22000).absolute()
        self.hashcat_args = tuple(hashcat_args)
        self.fast = fast
        self.verbose = verbose
        self.key_file = self.file_22000.with_suffix('.key')
        self.session = self.file_22000.name
        self.runner = self._default_runner

    def _default_runner(self, cmd: HashcatCmdCapture):
        subprocess_call(cmd.build())

    def new_cmd(self, hcap_file: Union[str, Path] = None):
        if hcap_file is None:
            hcap_file = self.file_22000
        return HashcatCmdCapture(hcap_file=hcap_file, outfile=self.key_file, hashcat_args=self.hashcat_args,
                                 session=self.session)

    def run_essid_attack(self):
        """
        Run ESSID + digits_append.txt combinator attack.
        Run ESSID + best64.rule attack.
        """
        ESSID_TRIED.parent.mkdir(parents=True, exist_ok=True)
        split_by_essid_dir = Path(tempfile.mkdtemp())

        bssid_essid_tried = set()
        if ESSID_TRIED.exists():
            with open(ESSID_TRIED, 'r') as f:
                bssid_essid_tried = set(f.read().splitlines())

        bssid_essid_pairs = tuple(bssid_essid_from_22000(self.file_22000))
        if len(bssid_essid_pairs) > 1:
            split_by_essid(self.file_22000, to_folder=split_by_essid_dir)
            files_split_by_essid = list(split_by_essid_dir.iterdir())
        else:
            files_split_by_essid = [self.file_22000]

        for hcap_fpath_essid in tqdm(files_split_by_essid, desc="ESSID attack",
                                     disable=not self.verbose):
            bssid_essid = next(bssid_essid_from_22000(hcap_fpath_essid))
            if bssid_essid in bssid_essid_tried:
                continue
            bssid, essid = bssid_essid.split(':')
            essid = decode_essid_hex(essid)
            hashcat_cmd = self.new_cmd(hcap_file=hcap_fpath_essid)
            
            # Note: run_essid_attack in word_magic.essid also takes a runner
            from app.word_magic.essid import run_essid_attack
            run_essid_attack(essid=essid, hashcat_cmd=hashcat_cmd,
                             fast=self.fast, runner=self.runner)

            with open(ESSID_TRIED, 'a') as f:
                f.write(bssid_essid + '\n')
        shutil.rmtree(split_by_essid_dir, ignore_errors=True)

    @monitor_timer
    def run_digits8(self):
        create_digits_wordlist()
        hashcat_cmd = self.new_cmd()
        hashcat_cmd.add_wordlists(WordList.DIGITS_8)
        self.runner(hashcat_cmd)

    @monitor_timer
    def run_top1k(self):
        create_fast_wordlists()
        hashcat_cmd = self.new_cmd()
        hashcat_cmd.add_wordlists(WordList.TOP1K_RULE_BEST64)
        self.runner(hashcat_cmd)

    @monitor_timer
    def run_keyboard_walk(self):
        if not WordList.KEYBOARD_WALK.path.exists():
            logger.warning(f"{WordList.KEYBOARD_WALK.path} does not exist. Skipping keyboard walk attack.")
            return
        hashcat_cmd = self.new_cmd()
        hashcat_cmd.add_wordlists(WordList.KEYBOARD_WALK)
        self.runner(hashcat_cmd)

    @monitor_timer
    def run_names(self):
        temp_outfile_handle = tempfile.NamedTemporaryFile(delete=False)
        temp_outfile = Path(temp_outfile_handle.name)
        temp_outfile_handle.close()
        try:
            hashcat_stdout = HashcatCmdStdout(outfile=temp_outfile, hashcat_args=self.hashcat_args)
            hashcat_stdout.add_wordlists(WordList.NAMES_UA_RU,
                                         WordList.NAMES_RU_CYRILLIC)
            hashcat_stdout.add_rule(Rule(Rule.ESSID))
            stdout, _ = subprocess_call(hashcat_stdout.build())

            temp_wordlist = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8')
            try:
                temp_wordlist.write(stdout)
                temp_wordlist.close()
                hashcat_cmd = self.new_cmd()
                hashcat_cmd.add_wordlists(temp_wordlist.name)
                self.runner(hashcat_cmd)
            finally:
                try:
                    temp_path = Path(temp_wordlist.name)
                    if temp_path.exists():
                        temp_path.unlink()
                except Exception:
                    pass
        finally:
            temp_outfile.unlink(missing_ok=True)

    @monitor_timer
    def run_names_with_digits(self):
        wordlist_order = [WordList.NAMES_UA_RU, WordList.DIGITS_APPEND_SHORT]
        with open(WordList.NAMES_UA_RU_WITH_DIGITS.path, 'w', encoding='utf-8') as f:
            for left in ['left', 'right']:
                for rule_names in ['', 'T0', 'u']:
                    temp_outfile_handle = tempfile.NamedTemporaryFile(delete=False)
                    temp_outfile = Path(temp_outfile_handle.name)
                    temp_outfile_handle.close()
                    try:
                        hashcat_stdout = HashcatCmdStdout(outfile=temp_outfile, hashcat_args=self.hashcat_args)
                        hashcat_stdout.add_wordlists(*wordlist_order, options=['-a1', f'--rule-{left}={rule_names}'])
                        stdout, _ = subprocess_call(hashcat_stdout.build())
                    finally:
                        temp_outfile.unlink(missing_ok=True)
                    f.write(stdout)
                    if stdout and not stdout.endswith('\n'):
                        f.write('\n')
                wordlist_order = wordlist_order[::-1]
        hashcat_cmd = self.new_cmd()
        hashcat_cmd.add_wordlists(WordList.NAMES_UA_RU_WITH_DIGITS)
        self.runner(hashcat_cmd)

    @monitor_timer
    def run_exhaustive_bruteforce(self, min_length=8, max_length=63):
        hashcat_args = list(self.hashcat_args)
        hashcat_args.extend([
            "--increment",
            f"--increment-min={min_length}",
            f"--increment-max={max_length}",
        ])
        hashcat_cmd = HashcatCmdCapture(
            hcap_file=self.file_22000,
            outfile=self.key_file,
            hashcat_args=tuple(hashcat_args),
            session=self.session,
        )
        hashcat_cmd.mask = self.WPA_EXHAUSTIVE_MASK
        self.runner(hashcat_cmd)

    def run_all(self):
        """
        Run all attacks.
        """
        self.run_top1k()
        self.run_digits8()
        self.run_keyboard_walk()
        self.run_essid_attack()
        self.run_names()


def crack_22000():
    """
    Crack .22000 in command line.
    """
    parser = argparse.ArgumentParser(description='Check weak passwords',
                                     usage="base_attack.py [-h] capture [hashcat-args]")
    parser.add_argument('capture', help='path to .22000')
    parser.add_argument('--fast', help='Run ESSID+digits attack with fewer examples. Default: turned off', action='store_true')
    parser.add_argument('--extra', help='Run extra attacks (names UA)', action='store_true')
    args, hashcat_args = parser.parse_known_args()
    print(f"Hashcat args: {hashcat_args}, fast={args.fast}, extra={args.extra}")
    attack = BaseAttack(file_22000=args.capture, hashcat_args=hashcat_args,
                        fast=args.fast)
    attack.run_all()
    if args.extra:
        print("Running extra run_names_with_digits attack")
        attack.run_names_with_digits()
    key_password = read_plain_key(attack.key_file)
    if key_password:
        print("WPA key is found!\n", key_password)
    else:
        print("WPA key is not found.")


if __name__ == '__main__':
    download_wordlists()
    crack_22000()
