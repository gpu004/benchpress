#!/bin/sh
set -eu

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dest=${1:?provide a destination directory}
version=26.13.1
variant=${BENCHPRESS_VARIANT:-}
if [ -f "$source_dir/.benchpress-variant" ]; then
    variant=$(tr -d '[:space:]' < "$source_dir/.benchpress-variant")
fi
variant=${variant:-default}
mkdir -p "$dest"

install_npm_tree() {
    npm_cli=$1
    npm_root=$(CDPATH= cd -- "$(dirname -- "$npm_cli")/.." && pwd)
    rm -rf "$dest/node_modules/npm"
    mkdir -p "$dest/node_modules"
    cp -R "$npm_root" "$dest/node_modules/npm"
}

if command -v node >/dev/null && [ "$(node -p process.versions.node)" = "$version" ]; then
    cp -L "$(command -v node)" "$dest/node"
    npm_cli=$(node -e '
      const fs = require("fs");
      const path = require("path");
      const candidates = [
        path.join(path.dirname(path.dirname(process.execPath)), "lib/node_modules/npm/bin/npm-cli.js"),
        path.resolve(path.dirname(process.execPath), "../lib/node_modules/npm/bin/npm-cli.js"),
        "/usr/lib/node_modules/npm/bin/npm-cli.js",
        "/opt/homebrew/lib/node_modules/npm/bin/npm-cli.js",
        "/usr/local/lib/node_modules/npm/bin/npm-cli.js",
      ];
      for (const candidate of candidates) {
        if (fs.existsSync(candidate)) {
          process.stdout.write(candidate);
          process.exit(0);
        }
      }
      process.exit(1);
    ')
    install_npm_tree "$npm_cli"
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
    tar -xJf "$tmp/$archive" -C "$tmp"
    extracted=$tmp/${archive%.tar.xz}
    cp "$extracted/bin/node" "$dest/node"
    install_npm_tree "$extracted/lib/node_modules/npm/bin/npm-cli.js"
fi

if [ ! -f "$dest/node_modules/npm/bin/npm-cli.js" ]; then
    echo 'npm was not installed into the payload destination' >&2
    exit 1
fi

cp "$source_dir/fixture.mjs" "$source_dir/run.mjs" "$source_dir/machine.mjs" \
   "$source_dir/cores-worker.mjs" "$dest/"
"$dest/node" "$dest/fixture.mjs"

pack_tmp=$(mktemp -d)
cp -R "$source_dir/install-package" "$pack_tmp/package"
tar -czf "$dest/install-package.tgz" -C "$pack_tmp" package
rm -rf "$pack_tmp"

filler="$dest/large-filler-256mib.bin"
if [ "$variant" = large ]; then
    dd if=/dev/zero of="$filler" bs=1048576 count=256 status=none
elif [ "$variant" = default ]; then
    rm -f "$filler"
else
    echo "Unknown BENCHPRESS_VARIANT=$variant" >&2
    exit 1
fi
