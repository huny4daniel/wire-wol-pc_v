"""릴리즈 빌드용 PyInstaller 버전 정보 파일(--version-file)을 만든다.

버전 정보 리소스가 비어있는 exe는 백신의 머신러닝 휴리스틱(Wacatac.B!ml 등)에
오탐되기 쉬운 특징 중 하나라, 깃 태그 기준 버전과 제품 설명을 exe에 넣어둔다.

사용법: python make_version_file.py <태그> <exe 파일명> <설명> <출력 경로>
"""
import re
import sys
from pathlib import Path

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver},
    prodvers={ver},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'WireWOL'),
         StringStruct('FileDescription', {description!r}),
         StringStruct('FileVersion', {ver_str!r}),
         StringStruct('InternalName', {internal!r}),
         StringStruct('LegalCopyright', 'Copyright (c) WireWOL'),
         StringStruct('OriginalFilename', {filename!r}),
         StringStruct('ProductName', 'WireWOL'),
         StringStruct('ProductVersion', {ver_str!r})])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def parse_version(tag: str) -> tuple:
    # v1.2.3 → (1, 2, 3, 0) — 숫자가 아닌 부분은 0으로 채운다.
    parts = [int(p) if p.isdigit() else 0 for p in re.sub(r'^v', '', tag).split('.')[:4]]
    return tuple(parts + [0] * (4 - len(parts)))


def main():
    tag, filename, description, output = sys.argv[1:5]
    ver = parse_version(tag)
    content = TEMPLATE.format(
        ver=ver,
        ver_str='.'.join(str(v) for v in ver[:3]),
        internal=Path(filename).stem,
        filename=filename,
        description=description,
    )
    Path(output).write_text(content, encoding='utf-8')


if __name__ == '__main__':
    main()
