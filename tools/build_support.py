"""Generate public Windows metadata and collect licenses from the build environment."""
import importlib.metadata
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from branding import VERSION,APP_NAME,APP_NAME_EN


def prepare():
    build=ROOT/'build'; build.mkdir(exist_ok=True)
    destination=build/'licenses'; destination.mkdir(exist_ok=True)
    for file in (ROOT/'licenses').iterdir():
        if file.is_file(): shutil.copy2(file,destination/file.name)
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.is_file(): raise RuntimeError('Python LICENSE.txt not found in the build environment')
    shutil.copy2(python_license,destination/'Python-LICENSE.txt')
    versions={'Python':sys.version.split()[0]}
    for package,label in [('Pillow','Pillow'),('pyinstaller','PyInstaller')]:
        distribution=importlib.metadata.distribution(package); versions[label]=distribution.version
        files=[f for f in distribution.files if 'licenses/' in f.as_posix() and Path(distribution.locate_file(f)).is_file()]
        if not files: raise RuntimeError(f'License missing for {package}')
        for index,relative in enumerate(files):
            source=Path(distribution.locate_file(relative))
            name=label+('-LICENSE.txt' if label=='Pillow' and index==0 else '-COPYING.txt' if label=='PyInstaller' and index==0 else '-'+source.name)
            shutil.copy2(source,destination/name)
    (destination/'BUILD-COMPONENTS.txt').write_text('\n'.join(f'{k}: {v}' for k,v in versions.items())+'\n',encoding='utf-8')
    numbers=tuple(int(x) for x in VERSION.split('.'))+(0,)
    strings={'FileDescription':f'{APP_NAME} {APP_NAME_EN}','FileVersion':VERSION,
             'InternalName':APP_NAME_EN,'OriginalFilename':f'{APP_NAME_EN}-{VERSION}.exe',
             'ProductName':f'{APP_NAME} {APP_NAME_EN}','ProductVersion':VERSION,
             'LegalCopyright':'Copyright (c) 2026 AniFerry contributors'}
    text=f'VSVersionInfo(ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f, flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable("080404B0", ['
    text+=','.join(f'StringStruct({k!r},{v!r})' for k,v in strings.items())
    text+='])]), VarFileInfo([VarStruct("Translation", [2052, 1200])])])\n'
    (build/'version-info.txt').write_text(text,encoding='utf-8')


if __name__=='__main__': prepare()
