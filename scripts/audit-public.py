"""Audit the exact Git staging area before publishing. Never prints matched secrets."""
import re
import subprocess
from pathlib import PurePosixPath


def git(*args):
    return subprocess.check_output(['git', *args])


def main():
    count = 0
    for name in filter(None, git('ls-files', '-z').decode().split('\0')):
        path = PurePosixPath(name)
        if any(part in {'.local', '.build', '.tools', 'node_modules', 'release', 'security'} for part in path.parts):
            raise SystemExit('Local directory staged: ' + name)
        if path.name.startswith('.env') or path.name == 'config.local.json' or path.suffix in {'.dpapi', '.pem', '.key', '.pfx', '.p12', '.msg', '.zip', '.exe', '.db', '.log'}:
            raise SystemExit('Private/local file staged: ' + name)
        if path.suffix == '.eml' and not name.startswith('tests/fixtures/'):
            raise SystemExit('Email outside synthetic fixtures: ' + name)
        raw = git('show', ':' + name)
        if re.search(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})', raw):
            raise SystemExit('Possible credential staged: ' + name)
        if re.search(rb'[A-Z]:\\Users\\', raw, re.I):
            raise SystemExit('Personal filesystem path staged: ' + name)
        if path.suffix in {'.json', '.html', '.js', '.cjs', '.py', '.eml', '.md'} and name != 'package-lock.json':
            for address in re.findall(rb'[\w.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})', raw):
                domain = address.decode().lower()
                if not any(domain == d or domain.endswith('.' + d) for d in ('example.com', 'example.org', 'example.net', 'example.test', 'ejemplo.com')):
                    raise SystemExit('Non-example email domain staged: ' + name)
        count += 1
    if not count:
        raise SystemExit('No staged files to audit.')
    print(f'Public source audit passed: {count} files.')


if __name__ == '__main__':
    main()
