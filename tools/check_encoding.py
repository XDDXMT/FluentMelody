"""Check Git-tracked text and names without printing file contents."""
from pathlib import Path
import argparse
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.nbs', '.mid', '.midi',
                   '.npz', '.wav', '.mp3', '.zip', '.pdf'}


def check(staged=False):
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
    failures = []
    count = 0
    for raw_name in filter(None, names):
        try:
            name = raw_name.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            failures.append('A tracked filename is not UTF-8.')
            continue
        if '\ufffd' in name:
            failures.append(f'{name}: replacement character in filename')
        if Path(name).suffix.lower() in BINARY_SUFFIXES:
            continue
        content = (subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
                   if staged else (ROOT / name).read_bytes())
        try:
            text = content.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            failures.append(f'{name}: text is not UTF-8')
            continue
        count += 1
        if content.startswith(b'\xef\xbb\xbf'):
            failures.append(f'{name}: remove UTF-8 BOM')
        if '\ufffd' in text or '\x00' in text:
            failures.append(f'{name}: replacement character or NUL in text')
        if Path(name).suffix.lower() != '.bat' and '\r' in text:
            failures.append(f'{name}: use LF line endings')
    for error in failures:
        print(error)
    print(f'Checked {count} UTF-8 text files; {len(failures)} errors.')
    return bool(failures)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--staged', action='store_true', help='Check the Git index instead of working files.')
    raise SystemExit(check(parser.parse_args().staged))
