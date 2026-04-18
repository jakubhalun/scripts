#!/usr/bin/env bash

root="${1:-.}"

find "$root" -name ".git" -type d | sed 's|/.git$||' | while IFS= read -r repo; do
    echo "==> $repo"
    if git -C "$repo" show-ref --verify --quiet refs/heads/main; then
        git -C "$repo" checkout main
    elif git -C "$repo" show-ref --verify --quiet refs/heads/master; then
        git -C "$repo" checkout master
    else
        echo "  [skip] No main or master branch found"
        continue
    fi
    git -C "$repo" pull
done
