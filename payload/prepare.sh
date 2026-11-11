#!/bin/sh
set -eu

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dest=${1:?provide a destination directory}
version=22.23.4
mkdir -p "$dest"

if command -v node >/dev/null && [ "$(node -p process.versions.node)" = "$version" ]; then
    cp -L "$(command -v node)" "$dest/node"
else
    case "$(uname -m)" in
        x86_64) arch=x64; sha=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce ;;
        aarch64|arm64) arch=arm64; sha=6287aa46b6c375d816e63ae66d024fbbfd70404825aabdfdf898a970871d2f4b ;;
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
