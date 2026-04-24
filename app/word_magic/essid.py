import re
import shutil
import tempfile
import uuid
from copy import deepcopy
from itertools import permutations
from pathlib import Path

import wordninja

from app.attack.hashcat_cmd import HashcatCmdStdout
from app.domain import Rule, WordList
from app.logger import logger
from app.utils import subprocess_call
from app.word_magic.hamming import hamming_ball

MAX_COMPOUNDS = 5  # max compounds for rule best64 attack


def _split_uppercase(word: str) -> set:
    """
    EverGreen -> Ever, Green
    """
    pos_upper = [pos for pos, letter in enumerate(word) if letter.isupper()]
    pos_upper.append(len(word))
    simple_words = set([])
    for left, right in zip(pos_upper[:-1], pos_upper[1:]):
        simple_words.add(word[left: right])
    return simple_words


def _word_compounds(word: str, min_length=2):
    return [compound for compound in wordninja.split(word) if len(compound) >= min_length]


def _word_compounds_permutation(word: str, max_compounds=MAX_COMPOUNDS, min_length=2, alpha_only=False):
    """
    catonsofa -> cat, on, sofa
    """
    compounds = _word_compounds(word, min_length=min_length)
    if alpha_only:
        compounds = filter(re.compile("[a-z]", flags=re.IGNORECASE).match, compounds)
    compounds = sorted(compounds, key=len, reverse=True)[:max_compounds]
    compounds_perm = list(compounds)
    for r in range(2, len(compounds) + 1):
        compounds_perm.extend(map(''.join, permutations(compounds, r)))
    return compounds_perm


def _collect_essid_parts(essid_origin: str, max_compounds=MAX_COMPOUNDS):
    def modify_case(word: str):
        return {word, word.lower(), word.upper(), word.capitalize(), word.lower().capitalize()}

    regex_non_char = re.compile('[^a-zA-Z]')
    essid_parts = {essid_origin}
    essid_parts.add(re.sub(r'\W+', '', essid_origin))
    essid_parts.add(re.sub('[^a-z]+', '', essid_origin, flags=re.IGNORECASE))
    essid_parts.update(_word_compounds_permutation(essid_origin, max_compounds=max_compounds))
    regex_split_parts = regex_non_char.split(essid_origin)
    regex_split_parts = list(filter(len, regex_split_parts))

    for word in regex_split_parts:
        essid_parts.update(_word_compounds_permutation(word, max_compounds=max_compounds))
        essid_parts.update(_word_compounds_permutation(word.lower(), max_compounds=max_compounds))

    essid_parts.update(regex_split_parts)
    essid_parts.update(_split_uppercase(essid_origin))
    for essid in list(essid_parts):
        essid = regex_non_char.sub('', essid)
        essid_parts.update(modify_case(essid))
    essid_parts.update(modify_case(essid_origin))
    essid_parts = set(word for word in essid_parts if len(word) > 1)
    essid_parts.update(modify_case(essid_origin))  # special case when ESSID is a single letter
    return essid_parts


def _collect_essid_hamming(essid: str, hamming_dist_max=1):
    essid_hamming = set()
    essid_hamming.update(hamming_ball(s=essid, n=hamming_dist_max))
    essid_hamming.update(hamming_ball(s=essid.lower(), n=hamming_dist_max))
    logger.debug(f"Essid {essid} -> {len(essid_hamming)} hamming cousins with dist={hamming_dist_max}")
    return essid_hamming


def _collect_essid_rule(essid_wordlist_path: Path):
    """
    Run ESSID + best64.rule attack.
    """
    temp_outfile = _new_temp_path()
    try:
        hashcat_stdout = HashcatCmdStdout(outfile=temp_outfile)
        hashcat_stdout.add_wordlists(essid_wordlist_path)
        hashcat_stdout.add_rule(Rule(Rule.ESSID))
        stdout, _ = subprocess_call(hashcat_stdout.build())
    finally:
        temp_outfile.unlink(missing_ok=True)
    return [line for line in stdout.splitlines() if line]


def _new_temp_path() -> Path:
    temp_handle = tempfile.NamedTemporaryFile(delete=False)
    try:
        return Path(temp_handle.name)
    finally:
        temp_handle.close()


def _write_candidates_to_tempfile(candidates: list[str]) -> Path:
    temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8')
    try:
        temp_file.write('\n'.join(candidates))
        if candidates:
            temp_file.write('\n')
    finally:
        temp_file.close()
    return Path(temp_file.name)


def _run_essid_digits(compounds_fpath: Path, hashcat_cmd=None, fast=True, runner=None):
    if not fast:
        assert hashcat_cmd is not None, \
            "Non-fast mode requires running a hashcat command."
    if runner is None:
        def runner(cmd: HashcatCmdStdout):
            subprocess_call(cmd.build())
    candidates = set()
    wordlist_order = [compounds_fpath]
    if fast:
        wordlist_order.append(WordList.DIGITS_APPEND_SHORT)
    else:
        wordlist_order.append(WordList.DIGITS_APPEND)
    
    hashcat_args = hashcat_cmd.hashcat_args if hashcat_cmd else []
    
    with open(compounds_fpath) as f:
        compounds_count = len(f.readlines())
    if compounds_count > 1000 and hashcat_cmd is not None:
        # reduce IO operations, run the hashcat attack directly
        fast = False
    for reverse in range(2):
        temp_outfile = _new_temp_path()
        try:
            hashcat_stdout = HashcatCmdStdout(outfile=temp_outfile, hashcat_args=hashcat_args)
            hashcat_stdout.add_wordlists(*wordlist_order, options=['-a1'])
            stdout, _ = subprocess_call(hashcat_stdout.build())
        finally:
            temp_outfile.unlink(missing_ok=True)
        stdout_candidates = [line for line in stdout.splitlines() if line]
        if fast:
            candidates.update(stdout_candidates)
        else:
            temp_path = _write_candidates_to_tempfile(stdout_candidates)
            try:
                _hashcat_cmd_tmp = deepcopy(hashcat_cmd)
                _hashcat_cmd_tmp.add_wordlists(temp_path)
                runner(_hashcat_cmd_tmp)
            finally:
                temp_path.unlink(missing_ok=True)
        wordlist_order = wordlist_order[::-1]
    return candidates


def run_essid_attack(essid, hashcat_cmd=None, fast=True, runner=None):
    # hashcat_cmd could be None for debug mode to check the no. of candidates
    if runner is None:
        def runner(cmd: HashcatCmdStdout):
            subprocess_call(cmd.build())

    password_candidates = set()
    essid_as_wordlist_dir = Path(tempfile.mkdtemp())

    # (1) Hamming ball attack
    essid_compounds = _collect_essid_parts(essid)
    # Limit the number of word compounds to an arbitrary number.
    if len(essid_compounds) < 100:
        for compound in essid_compounds:
            password_candidates.update(_collect_essid_hamming(essid=compound))
    else:
        password_candidates.update(_collect_essid_hamming(essid=essid))

    # (2) best64 rule attack
    # A random unique file path.
    compounds_fpath = essid_as_wordlist_dir / str(uuid.uuid4())
    compounds_fpath.write_text('\n'.join(essid_compounds))
    password_candidates.update(_collect_essid_rule(compounds_fpath))

    # (3) digits_append attack
    password_candidates.update(_run_essid_digits(compounds_fpath,
                                                  hashcat_cmd=hashcat_cmd,
                                                  fast=fast,
                                                  runner=runner))

    if hashcat_cmd is not None and password_candidates:
        temp_path = _write_candidates_to_tempfile(sorted(password_candidates))
        try:
            hashcat_cmd = deepcopy(hashcat_cmd)
            hashcat_cmd.add_wordlists(temp_path)
            runner(hashcat_cmd)
        finally:
            temp_path.unlink(missing_ok=True)

    shutil.rmtree(essid_as_wordlist_dir, ignore_errors=True)
    return password_candidates


if __name__ == '__main__':
    # run_essid_attack("lrtgn5s19b41e21f1202unc77i8093")
    run_essid_attack("MaloinvazivTrile_2.4GHz")
    run_essid_attack("PetitCafe")
    for essid in ["Tanya007", "My_rabbit", "Myrabbit", "MyRabbit", "PetitCafe2017"]:
        compounds = sorted(_word_compounds_permutation(essid))
        candidates = sorted(_collect_essid_parts(essid))
        print(f"'{essid}'\n\t{len(compounds)} compounds: {compounds}")
        print(f"\t{len(candidates)} candidates: {candidates}")
