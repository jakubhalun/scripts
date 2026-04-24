# Scripts

A collection of useful scripts and instructions for everyday development tasks.

---

## Git

Shell scripts for batch operations on multiple local repositories:

| Script | Description |
|--------|-------------|
| [`git/currentBranchInfoAll.sh`](git/currentBranchInfoAll.sh) | Print current branch for every git repository in the current directory |
| [`git/mainBranchAndPull.sh`](git/mainBranchAndPull.sh) | Switch to `main` (or `master`) branch and run `git pull` for every git repository found at any depth below the given directory (default: current) |
| [`git/pullAll.sh`](git/pullAll.sh) | Run `git pull` on every git repository in the current directory |

Windows CMD equivalents (in `git/windows_cmd/`):

| Script | Description |
|--------|-------------|
| [`git/windows_cmd/pull_all_repos_from_subdirs.bat`](git/windows_cmd/pull_all_repos_from_subdirs.bat) | `git pull origin` on every subdirectory |
| [`git/windows_cmd/all_repos_from_subdirs_checkout_master_branch.bat`](git/windows_cmd/all_repos_from_subdirs_checkout_master_branch.bat) | Checkout `master` branch in every subdirectory |

### Clone All Repositories

[`git/clone_all_repos.py`](git/clone_all_repos.py) — clone **all** GitHub repositories
owned by the authenticated user into an organized directory structure.
Repositories that already exist in the target directory are skipped, so the
script is safe to run repeatedly (e.g. to pick up newly created repos).

```
<target_dir>/
├── public/       # public repositories
├── private/      # private repositories
│   └── shared/   # private repos shared with outside collaborators
└── profile/      # the user's profile repo (username/username)
```

**Setup:**

1. Create a fine-grained personal access token at
   <https://github.com/settings/tokens>
2. Token configuration:
   - **Repository Access** → All repositories
   - **Permissions** → Contents: *read-only*, Administration: *read-only*
3. Save the token as `GITHUB_API_KEY` in a `.env` file

**Usage:**

```bash
pip install requests python-dotenv
python git/clone_all_repos.py USERNAME [TARGET_DIR]          # SSH (default)
python git/clone_all_repos.py USERNAME [TARGET_DIR] --https  # HTTPS with token
```

---

## Commands

Handy one-liners collected in [`commands.md`](commands.md).

---

## Instructions

Step-by-step guides:

- [Copy code between git repositories with preserved history](instructions/copy_between_git_repos_with_history.md)

---

## Varia

| Script | Description |
|--------|-------------|
| [`varia/download-files-from-webpage.ps1`](varia/download-files-from-webpage.ps1) | Download media files from a webpage, especially useful for plain "index of" directory listings |
