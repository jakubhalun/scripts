#!/usr/bin/env python3
"""
Clone all GitHub repositories owned by the authenticated user.

Repositories that already exist in the target directory structure are
skipped automatically, so the script is safe to run repeatedly (e.g. to
pick up newly created repos).

By default, repositories are cloned over SSH (git@github.com:...).
Pass --https to clone over HTTPS with token authentication instead.

Setup:
  1. Create a fine-grained personal access token at:
     https://github.com/settings/tokens
  2. Token configuration:
     - Repository Access: All repositories
     - Permissions:
       - Contents: read-only       (clone repos, list repo metadata)
       - Administration: read-only  (detect shared private repos)
  3. Save the token as GITHUB_API_KEY in a .env file
     (the .env file should be in the working directory or the script directory)

Commands:
  pip install requests python-dotenv
  python clone_all_repos.py USERNAME [TARGET_DIR]
  python clone_all_repos.py USERNAME [TARGET_DIR] --https
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    print("Missing dependencies. Install them with:")
    print("  pip install requests python-dotenv")
    sys.exit(1)


GITHUB_API_URL = "https://api.github.com"


def api_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def get_owned_repos(token):
    repos = []
    page = 1
    while True:
        resp = requests.get(
            f"{GITHUB_API_URL}/user/repos",
            headers=api_headers(token),
            params={"affiliation": "owner", "per_page": 100, "page": page},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return repos


def has_outside_collaborators(token, owner, repo_name):
    """Return True if a repo has collaborators other than the owner.

    Returns None if the information is unavailable (e.g. insufficient
    token permissions).
    """
    try:
        resp = requests.get(
            f"{GITHUB_API_URL}/repos/{owner}/{repo_name}/collaborators",
            headers=api_headers(token),
            params={"affiliation": "outside", "per_page": 1},
        )
        if resp.status_code == 200:
            return len(resp.json()) > 0
    except requests.RequestException:
        pass
    return None


def clone_repo(username, token, full_name, target_dir, *, use_ssh=True):
    repo_name = full_name.split("/")[-1]
    dest = target_dir / repo_name

    if dest.exists():
        print(f"  [skip] {repo_name} (already exists)")
        return

    if use_ssh:
        clone_url = f"git@github.com:{full_name}.git"
    else:
        clone_url = f"https://{username}:{token}@github.com/{full_name}.git"
    print(f"  [clone] {repo_name} ...")
    result = subprocess.run(
        ["git", "clone", "--quiet", clone_url, str(dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [error] {repo_name}: {result.stderr.strip()}")
    else:
        print(f"  [ok]    {repo_name}")


def main():
    parser = argparse.ArgumentParser(
        description="Clone all GitHub repositories owned by the authenticated user.",
    )
    parser.add_argument("username", help="GitHub username")
    parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="target directory (default: current directory)",
    )
    parser.add_argument(
        "--https",
        action="store_true",
        dest="use_https",
        help="clone over HTTPS with token auth (default: SSH)",
    )
    args = parser.parse_args()

    # Load .env from CWD, then from the script's own directory as fallback
    load_dotenv()
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")

    token = os.getenv("GITHUB_API_KEY")
    if not token:
        print("Error: GITHUB_API_KEY not found in environment or .env file.")
        print("See the script header for setup instructions.")
        sys.exit(1)

    username = args.username
    use_ssh = not args.use_https
    target = Path(args.target_dir).resolve()

    # Fetch repos
    print(f"Fetching repositories for {username} ...")
    try:
        repos = get_owned_repos(token)
    except requests.HTTPError as exc:
        print(f"GitHub API error: {exc}")
        sys.exit(1)

    public_repos = [r for r in repos if not r["private"]]
    private_repos = [r for r in repos if r["private"]]
    print(f"Found {len(repos)} repositories "
          f"({len(public_repos)} public, {len(private_repos)} private).\n")

    # Prepare directories
    public_dir = target / "public"
    private_dir = target / "private"
    shared_dir = private_dir / "shared"
    profile_dir = target / "profile"

    public_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # --- Public repos ---
    if public_repos:
        print(f"Public repositories ({len(public_repos)}):")
        for repo in sorted(public_repos, key=lambda r: r["name"].lower()):
            clone_repo(username, token, repo["full_name"], public_dir,
                       use_ssh=use_ssh)
        print()

    # --- Private repos (with optional shared detection) ---
    if private_repos:
        shared = []
        non_shared = []
        can_detect = True

        for repo in private_repos:
            result = has_outside_collaborators(token, username, repo["name"])
            if result is None:
                can_detect = False
                break
            (shared if result else non_shared).append(repo)

        if can_detect and shared:
            shared_dir.mkdir(parents=True, exist_ok=True)

            print(f"Private repositories ({len(non_shared)}):")
            for repo in sorted(non_shared, key=lambda r: r["name"].lower()):
                clone_repo(username, token, repo["full_name"], private_dir,
                           use_ssh=use_ssh)
            print()

            print(f"Shared private repositories ({len(shared)}):")
            for repo in sorted(shared, key=lambda r: r["name"].lower()):
                clone_repo(username, token, repo["full_name"], shared_dir,
                           use_ssh=use_ssh)
            print()
        else:
            if not can_detect:
                print("Note: Cannot detect shared repos "
                      "(token may lack Administration permission).")
                print("All private repos will be placed in private/.\n")
            print(f"Private repositories ({len(private_repos)}):")
            for repo in sorted(private_repos, key=lambda r: r["name"].lower()):
                clone_repo(username, token, repo["full_name"], private_dir,
                           use_ssh=use_ssh)
            print()

    # --- Profile repo (username/username) ---
    print("Profile repository:")
    clone_repo(username, token, f"{username}/{username}", profile_dir,
               use_ssh=use_ssh)
    print()

    print("Done.")


if __name__ == "__main__":
    main()
