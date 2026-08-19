#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${TRITONDFT_VESTA_DIR:-${ROOT_DIR}/local/VESTA}"
VESTA_URL="https://www.jp-minerals.org/vesta/archives/3.5.8/VESTA-gtk3.tar.bz2"
LICENSE_URL="https://www.jp-minerals.org/vesta/en/download.html"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This installer is for Linux. Use the official VESTA package for your operating system."
    exit 2
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
    echo "This pinned stable package supports Linux x86_64; detected $(uname -m)."
    echo "Download the appropriate build from: ${LICENSE_URL}"
    exit 2
fi
if [[ "${1:-}" != "--accept-license" ]]; then
    echo "Review the VESTA license before downloading:"
    echo "  ${LICENSE_URL}"
    echo "Then run: bash scripts/install_vesta_linux.sh --accept-license"
    exit 2
fi

command -v tar >/dev/null || { echo "tar is required."; exit 1; }
if command -v curl >/dev/null; then
    DOWNLOADER=(curl -fL)
elif command -v wget >/dev/null; then
    DOWNLOADER=(wget -O)
else
    echo "curl or wget is required."
    exit 1
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tritondft-vesta.XXXXXX")"
ARCHIVE="${TEMP_DIR}/VESTA-gtk3.tar.bz2"
cleanup() { rm -rf "${TEMP_DIR}"; }
trap cleanup EXIT

mkdir -p "${INSTALL_DIR}"
if [[ "${DOWNLOADER[0]}" == "curl" ]]; then
    "${DOWNLOADER[@]}" "${VESTA_URL}" -o "${ARCHIVE}"
else
    "${DOWNLOADER[@]}" "${ARCHIVE}" "${VESTA_URL}"
fi
tar -xjf "${ARCHIVE}" -C "${INSTALL_DIR}"

VESTA_BIN=""
while IFS= read -r candidate; do
    VESTA_BIN="${candidate}"
    break
done < <(find "${INSTALL_DIR}" -maxdepth 4 -type f -name VESTA -perm -u+x)
if [[ -z "${VESTA_BIN}" ]]; then
    echo "The archive was extracted, but no executable VESTA file was found in ${INSTALL_DIR}."
    exit 1
fi
if [[ "${VESTA_BIN}" != "${INSTALL_DIR}/VESTA" ]]; then
    ln -sfn "${VESTA_BIN}" "${INSTALL_DIR}/VESTA"
fi

echo "VESTA installed: ${INSTALL_DIR}/VESTA"
if command -v ldd >/dev/null && ldd "${INSTALL_DIR}/VESTA" | grep -q "not found"; then
    echo "Warning: missing shared libraries:"
    ldd "${INSTALL_DIR}/VESTA" | grep "not found" || true
    echo "Ask the cluster administrator to provide the missing GTK/OpenGL libraries."
fi
echo "TritonDFT will discover this repository-local installation automatically."
echo "For direct shell use: export PATH=\"${INSTALL_DIR}:\$PATH\""

