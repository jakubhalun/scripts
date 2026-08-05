#!/usr/bin/env bash
#
# flatten_branch_history.sh
#
# Replaces the full history of an existing branch with a single new commit and
# force-pushes that rewritten branch to a remote repository.
#
# Functions:
#   usage
#     Prints command usage and exits.
#   require_git_repository
#     Verifies that the script is running inside a Git working tree.
#   require_clean_worktree
#     Refuses to continue when tracked or untracked changes are present.
#   confirm_destructive_action
#     Shows a destructive-operation warning and requires explicit confirmation.
#   flatten_branch_history
#     Creates an orphan branch, commits the current tree, replaces the target
#     branch locally, and force-pushes it with --force-with-lease.
#
# Parameters:
#   <branch>
#     Required. Name of the branch whose history will be replaced.
#   -m, --message <message>
#     Optional. Commit message for the new single commit.
#     Default: "Initial commit (flattened history)".
#   -r, --remote <remote>
#     Optional. Remote name to push to. Default: "origin".
#   -y, --yes
#     Optional. Skip the interactive prompt. This still requires setting
#     FLATTEN_BRANCH_HISTORY_CONFIRM=YES in the environment.
#   -h, --help
#     Show usage information.
#
# Warning:
#   This script rewrites Git history and force-pushes the selected branch.
#   Make sure collaborators are aware before running it.

set -euo pipefail

DEFAULT_COMMIT_MESSAGE="Initial commit (flattened history)"
CONFIRMATION_TEXT="REPLACE HISTORY"

usage() {
    cat <<'USAGE'
Usage:
  bash git/flatten_branch_history.sh <branch> [options]

Options:
  -m, --message <message>  Commit message for the new single commit.
  -r, --remote <remote>    Remote name to push to. Default: origin.
  -y, --yes                Skip the prompt only when FLATTEN_BRANCH_HISTORY_CONFIRM=YES is set.
  -h, --help               Show this help message.

This script replaces the complete history of <branch> with one new commit and
pushes the rewritten branch using git push --force-with-lease.

Interactive confirmation is mandatory unless both --yes and the environment
variable FLATTEN_BRANCH_HISTORY_CONFIRM=YES are used.
USAGE
}

require_git_repository() {
    git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
        printf 'Error: this command must be run inside a Git repository.\n' >&2
        exit 1
    }
}

require_clean_worktree() {
    if [ -n "$(git status --porcelain)" ]; then
        printf 'Error: working tree is not clean. Commit or remove local changes first.\n' >&2
        exit 1
    fi
}

confirm_destructive_action() {
    branch=$1
    remote=$2
    assume_yes=$3

    cat <<WARNING
WARNING: This will permanently rewrite the history of branch '$branch'.
The script will create one new commit from the current files, replace the local
'$branch' branch, and force-push it to '$remote/$branch'.

Anyone using this branch will need to resynchronize their local clone.
WARNING

    if [ "$assume_yes" = "true" ]; then
        if [ "${FLATTEN_BRANCH_HISTORY_CONFIRM:-}" = "YES" ]; then
            return 0
        fi

        printf 'Error: --yes requires FLATTEN_BRANCH_HISTORY_CONFIRM=YES.\n' >&2
        exit 1
    fi

    printf 'Type "%s" to continue: ' "$CONFIRMATION_TEXT"
    read -r confirmation

    if [ "$confirmation" != "$CONFIRMATION_TEXT" ]; then
        printf 'Aborted. Confirmation did not match.\n' >&2
        exit 1
    fi
}

flatten_branch_history() {
    branch=$1
    remote=$2
    commit_message=$3

    temp_branch="flatten-history-$(date +%Y%m%d%H%M%S)-$$"

    git fetch "$remote" "$branch"
    git checkout "$branch"
    git checkout --orphan "$temp_branch"
    git add -A
    git commit -m "$commit_message"
    git branch -D "$branch"
    git branch -m "$branch"
    git push --force-with-lease "$remote" "$branch"
}

branch=""
remote="origin"
commit_message=$DEFAULT_COMMIT_MESSAGE
assume_yes="false"

while [ "$#" -gt 0 ]; do
    case "$1" in
        -m|--message)
            if [ "$#" -lt 2 ]; then
                printf 'Error: %s requires a value.\n' "$1" >&2
                exit 1
            fi
            commit_message=$2
            shift 2
            ;;
        -r|--remote)
            if [ "$#" -lt 2 ]; then
                printf 'Error: %s requires a value.\n' "$1" >&2
                exit 1
            fi
            remote=$2
            shift 2
            ;;
        -y|--yes)
            assume_yes="true"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -* )
            printf 'Error: unknown option: %s\n' "$1" >&2
            usage >&2
            exit 1
            ;;
        *)
            if [ -n "$branch" ]; then
                printf 'Error: only one branch name can be provided.\n' >&2
                usage >&2
                exit 1
            fi
            branch=$1
            shift
            ;;
    esac
done

if [ -z "$branch" ]; then
    printf 'Error: branch name is required.\n' >&2
    usage >&2
    exit 1
fi

require_git_repository
require_clean_worktree
confirm_destructive_action "$branch" "$remote" "$assume_yes"
flatten_branch_history "$branch" "$remote" "$commit_message"
