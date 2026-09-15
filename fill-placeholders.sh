#!/usr/bin/env bash
# Replace placeholders in every repository.
#
#   ./fill-placeholders.sh <github-org> "<Your Name>" <your-domain.com> [package-name]
#
# github-org must be lowercase (Docker image names on ghcr.io must be lowercase).
# package-name is optional: use it only if "tokenquota" is taken on PyPI or npm
# by the time you publish. It must be lowercase letters, digits or underscores.
set -euo pipefail

if [[ $# -lt 3 ]]; then
  sed -n '2,9p' "$0"; exit 1
fi
ORG="$1"; NAME="$2"; DOMAIN="$3"; PKG="${4:-tokenquota}"

[[ "$ORG" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "github-org must be lowercase letters, digits and hyphens"; exit 1; }
[[ "$PKG" =~ ^[a-z][a-z0-9_]*$ ]] || { echo "package-name must be lowercase letters, digits and underscores"; exit 1; }

cd "$(dirname "$0")"
SELF="$(basename "$0")"

files=$(grep -rlI --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=dist --exclude="$SELF" \
        -e 'YOUR-ORG' -e 'YOUR NAME' -e 'YOUR-DOMAIN' -e 'tokenquota' -e 'TOKENQUOTA' . || true)

for f in $files; do
  ORG="$ORG" NAME="$NAME" DOMAIN="$DOMAIN" PKG="$PKG" PKGU="$(echo "$PKG" | tr a-z A-Z)" \
  perl -pi -e 's/YOUR-ORG/$ENV{ORG}/g; s/YOUR NAME/$ENV{NAME}/g; s/YOUR-DOMAIN/$ENV{DOMAIN}/g;
                if ($ENV{PKG} ne "tokenquota") { s/tokenquota/$ENV{PKG}/g; s/TOKENQUOTA/$ENV{PKGU}/g; }' "$f"
done

if [[ "$PKG" != "tokenquota" ]]; then
  mv quota-python/src/tokenquota "quota-python/src/$PKG"
  mv quota-server/src/tokenquota_server "quota-server/src/${PKG}_server"
fi

echo "Done. Remaining placeholders (should be none):"
grep -rnI --exclude-dir=node_modules --exclude-dir=.git --exclude="$SELF" -e 'YOUR-ORG' -e 'YOUR NAME' -e 'YOUR-DOMAIN' . || echo "  none"
