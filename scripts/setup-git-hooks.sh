#!/bin/sh
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

git config core.hooksPath .githooks

printf 'Git hooks enabled: core.hooksPath=.githooks\n'
printf 'Use SKIP_GIT_GUARDS=1 only for an intentional emergency bypass.\n'
