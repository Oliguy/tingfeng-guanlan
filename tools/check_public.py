"""Check the tracked/staged publication surface, never inspect user data directories."""
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def main():
    try:
        names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
        files=[ROOT/n for n in names if n]
    except (subprocess.CalledProcessError,FileNotFoundError):
        files=[p for folder in ('src','tests','docs','examples','.github','tools') for p in (ROOT/folder).rglob('*')
               if p.is_file() and not any(x=='__pycache__' or x.endswith('.egg-info') for x in p.parts)]
        files += [ROOT/n for n in ('README.md','AGENTS.md','pyproject.toml','.gitignore','.gitattributes')]
    errors=[]
    forbidden={'.sqlite','.db','.parquet','.zip','.pem','.pfx','.key','.pyc','.log'}
    allowed_binary={'.png','.ico'}
    private=re.compile(r'(?:[A-Za-z]:[/\\](?:Users|UserData|StockOperator|CodexData|TingFengGuanLan)|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
    for p in files:
        if not p.is_file():continue
        if not p.resolve().is_relative_to(ROOT) or p.suffix.lower() in forbidden or '.sqlite' in p.name or p.name.startswith('.env'):
            errors.append(str(p.relative_to(ROOT))+': forbidden publication file');continue
        if p.suffix in allowed_binary:continue
        try:t=p.read_text('utf-8-sig')
        except UnicodeError:errors.append(str(p.relative_to(ROOT))+': unexpected binary');continue
        if private.search(t):errors.append(str(p.relative_to(ROOT))+': private path or credential pattern')
    if errors:
        print('\n'.join(errors));return 1
    print(f'Public surface checked: {len(files)} files; no database, credential or local workspace paths found.')
    return 0

if __name__=='__main__':raise SystemExit(main())
