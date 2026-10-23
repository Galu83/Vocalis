#!/bin/sh
# Construit dist/Vocalis-Setup.exe (nécessite makensis : apt install nsis).
set -e
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' vocalis/__init__.py)
rm -rf build/payload
mkdir -p build/payload dist
cp -r vocalis requirements.txt requirements-gpu.txt README.md build/payload/
cp installer/vocalis.ico build/payload/vocalis.ico
find build/payload -name __pycache__ -type d -prune -exec rm -rf {} +
makensis -V2 -DVERSION="$VERSION" -DPAYLOAD="$(pwd)/build/payload" \
    -DOUTFILE="$(pwd)/dist/Vocalis-Setup.exe" installer/vocalis.nsi
rm -rf build
echo "dist/Vocalis-Setup.exe ($VERSION)"
