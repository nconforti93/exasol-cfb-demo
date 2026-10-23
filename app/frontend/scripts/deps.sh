#!/bin/sh
# Installs the frontend's dependencies (`npm ci`) when package.json or the lockfile changed.
#
# On a Windows drive (the repo under /mnt/c, synced by OneDrive) node_modules is installed into a
# native directory and linked in: reading the thousands of small files of node_modules through the
# drive takes minutes (loading jsdom alone: 3.5 minutes against 2.4 seconds), and vitest gives up
# when its workers start slower than 60 seconds. Elsewhere node_modules stays in place.
#   CFB_FRONTEND_DEPS   the native directory (default ~/.cache/cfb-app-frontend)
# To change a dependency, edit package.json, run `npm install --package-lock-only`, then make app-deps.
set -eu

cd "$(dirname "$0")/.."
here=$(pwd)

case "$here" in
/mnt/*) store="${CFB_FRONTEND_DEPS:-$HOME/.cache/cfb-app-frontend}" ;;
*) store="" ;;
esac

if [ -z "$store" ]; then
  if [ ! -d node_modules ] || [ package-lock.json -nt node_modules ]; then
    npm ci
  fi
  exit 0
fi

mkdir -p "$store"
if [ ! -d "$store/node_modules" ] || ! cmp -s package.json "$store/package.json" || ! cmp -s package-lock.json "$store/package-lock.json"; then
  cp package.json package-lock.json "$store/"
  (cd "$store" && npm ci)
fi

if [ ! -L node_modules ] || [ "$(readlink node_modules)" != "$store/node_modules" ]; then
  rm -rf node_modules
  ln -s "$store/node_modules" node_modules
fi
