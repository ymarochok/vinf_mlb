#!/usr/bin/env bash
# Official full English Wikipedia articles dump (~27 GB compressed).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p input
cd input
base="${1:-https://dumps.wikimedia.org/enwiki/latest}"
base="${base%/}"
snapshot="${base##*/}"
curl --fail --location --retry 5 "$base/enwiki-${snapshot}-sha1sums.txt" -o sha1sums.txt
file="$(awk '$2 ~ /^\*?enwiki-[0-9]+-pages-articles-multistream\.xml\.bz2$/ {sub(/^\*/, "", $2); print $2; exit}' sha1sums.txt)"
if [[ -z "$file" ]]; then
    echo "No full articles dump found in the checksum list: $base" >&2
    exit 1
fi
# The 'latest' checksum list contains dated filenames. Pin that snapshot.
snapshot="${file#enwiki-}"
snapshot="${snapshot%%-*}"
base="${base%/*}/$snapshot"
awk -v f="$file" '$2 == f || $2 == "*" f' sha1sums.txt > "$file.sha1"
test -s "$file.sha1"
if ! sha1sum --check "$file.sha1" 2>/dev/null; then
    curl --fail --location --retry 5 --continue-at - "$base/$file" -o "$file.part"
    expected="$(awk '{print $1}' "$file.sha1")"
    printf '%s  %s\n' "$expected" "$file.part" | sha1sum --check -
    mv "$file.part" "$file"
fi
printf 'Source: %s/%s\nDownloaded: %s\n' "$base" "$file" "$(date -u +%FT%TZ)" > source.txt
cat "$file.sha1" >> source.txt
