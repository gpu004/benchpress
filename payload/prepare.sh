#!/bin/sh
set -eu

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dest=${1:?provide a destination directory}
version=26.13.1
mkdir -p "$dest"

if command -v node >/dev/null && [ "$(node -p process.versions.node)" = "$version" ]; then
    cp -L "$(command -v node)" "$dest/node"
else
    case "$(uname -m)" in
        x86_64) arch=x64; sha=5c063e46bab0fbc1c17cc445d7ff129cbdcd0471be6ed83b6ab864f8143bc864 ;;
        aarch64|arm64) arch=arm64; sha=89e1853138db51e8b82d3e98ba9c0d3f2956a28e5e55ea1b7e12d25aae92d5dd ;;
        *) echo 'Unsupported Linux architecture' >&2; exit 1 ;;
    esac
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    archive=node-v$version-linux-$arch.tar.xz
    curl -fsSL --retry 2 "https://nodejs.org/dist/v$version/$archive" -o "$tmp/$archive"
    (cd "$tmp" && printf '%s  %s\n' "$sha" "$archive" | sha256sum -c -)
    tar -xJf "$tmp/$archive" -C "$tmp" "${archive%.tar.xz}/bin/node"
    cp "$tmp/${archive%.tar.xz}/bin/node" "$dest/node"
fi

cp "$source_dir/fixture.mjs" "$source_dir/run.mjs" "$source_dir/machine.mjs" "$dest/"
"$dest/node" "$dest/fixture.mjs"
