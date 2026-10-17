"""Export an audited allowlist; never upload, push, or include a local profile."""
import argparse
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from branding import APP_NAME_EN,VERSION

FILES=('app.py','appearance.py','branding.py','core.py','details.py','downloaders.py',
       'test_core.py','test_details.py','test_downloaders.py','test_ui.py',
       'README.md','LICENSE','THIRD_PARTY_NOTICES.md','CONTRIBUTING.md','SECURITY.md','CHANGELOG.md',
       'requirements.txt','requirements-build.txt','build.ps1','start.bat','.gitignore','.gitattributes')
DIRECTORIES=('assets','docs','licenses','.github','tools')
FORBIDDEN_SUFFIXES={'.sqlite','.sqlite3','.db','.torrent','.pyc','.pyo','.log'}
PATTERNS=(r'gh[pousr]_[A-Za-z0-9]{30,}',r'github_pat_[A-Za-z0-9_]{30,}',
          r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'dpapi:[A-Za-z0-9+/=]{40,}',
          r'[A-Za-z]:[\\/](?:Users[\\/][^\s\n\r]+|desk exe[\\/]|下载[\\/])')


def public_files():
    selected=[ROOT/name for name in FILES]
    for directory in DIRECTORIES:
        folder=ROOT/directory
        if not folder.is_dir(): raise ValueError('Missing public directory: '+directory)
        selected.extend(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    for path in selected:
        if not path.is_file(): raise ValueError('Missing public file: '+path.name)
        if path.is_symlink() or path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith('.env'):
            raise ValueError('Forbidden public file: '+str(path.relative_to(ROOT)))
        if path.suffix.lower() not in ('.png','.ico'):
            text=path.read_text(encoding='utf-8')
            for pattern in PATTERNS:
                if re.search(pattern,text): raise ValueError('Sensitive-looking content in '+str(path.relative_to(ROOT)))
    return sorted(set(selected))


def audit_binary(archive):
    with zipfile.ZipFile(archive) as package:
        if package.testzip(): raise ValueError('Corrupt binary ZIP')
        for item in package.infolist():
            path=Path(item.filename)
            if path.is_absolute() or '..' in path.parts or path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith('.env'):
                raise ValueError('Unexpected binary entry: '+item.filename)
            if any(part in {'bangumi-cache','torrents','backups','.git'} for part in path.parts):
                raise ValueError('Profile material in binary: '+item.filename)
        prefix=f'{APP_NAME_EN}-{VERSION}/'
        required=[prefix+f'{APP_NAME_EN}-{VERSION}.exe',prefix+'LICENSE',prefix+'licenses/Python-LICENSE.txt',
                  prefix+'licenses/Pillow-LICENSE.txt',prefix+'licenses/PyInstaller-COPYING.txt',
                  prefix+'licenses/Tcl-license.terms',prefix+'licenses/Tk-license.terms',prefix+'docs/USAGE.md']
        for name in required:
            if name not in package.namelist(): raise ValueError('Missing release component: '+name)
        return len(package.infolist())


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path); args=parser.parse_args()
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    destination=args.output or ROOT/'publishing'/f'{APP_NAME_EN}-{VERSION}-{stamp}'
    if destination.exists(): raise ValueError('Output already exists; choose a new directory')
    paths=public_files()
    binary=ROOT/'dist'/f'{APP_NAME_EN}-{VERSION}-Windows-x64.zip'
    entries=audit_binary(binary)
    repository=destination/'repository'; repository.mkdir(parents=True)
    for path in paths:
        relative=path.relative_to(ROOT); target=repository/relative
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
    source_zip=destination/f'{APP_NAME_EN}-{VERSION}-Source.zip'
    with zipfile.ZipFile(source_zip,'w',zipfile.ZIP_DEFLATED) as package:
        for path in paths:
            relative=path.relative_to(ROOT)
            package.write(repository/relative,Path(f'{APP_NAME_EN}-{VERSION}-source')/relative)
    shutil.copy2(binary,destination/binary.name)
    for source,target in [('docs/RELEASE_NOTES.md','RELEASE_NOTES.md'),('docs/PUBLISHING.md','UPLOAD_GUIDE.md')]:
        shutil.copy2(ROOT/source,destination/target)
    archives=[source_zip,destination/binary.name]
    sums={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in archives}
    (destination/'SHA256SUMS.txt').write_text(''.join(f'{digest}  {name}\n' for name,digest in sums.items()),encoding='utf-8')
    report=dict(product=APP_NAME_EN,version=VERSION,source_files=len(paths),binary_entries=entries,
                profile_data_included=False,personal_screenshots_included=False,remote_upload_performed=False,
                license='MIT',checksums=sums,files=[str(p.relative_to(ROOT)).replace('\\','/') for p in paths])
    (destination/'PREPARATION_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(source_zip) as package:
        if package.testzip(): raise ValueError('Corrupt source ZIP')
    print(str(destination))
    print(f'Public source files: {len(paths)}; binary entries: {entries}; no upload performed.')


if __name__=='__main__': main()
