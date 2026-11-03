#!/usr/bin/env python3
"""Offline, read-only export of GitHub Copilot CLI conversations for one local repo.

Python 3.9+, standard library only. Does not call Copilot, git, or any network service.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def yaml_fields(path):
    """Read common top-level scalar workspace.yaml metadata; no YAML dependency."""
    fields = {}
    if not path.exists():
        return fields
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        match = re.match(r'^([A-Za-z_][\w-]*):\s*(.*?)\s*$', line)
        if not match:
            continue
        key, value = match.groups()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            if value[0] == '"':
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = value[1:-1]
            else:
                value = value[1:-1].replace("''", "'")
        fields[key] = value
    return fields


def read_events(path):
    events, warnings = [], []
    with path.open(encoding="utf-8-sig", errors="replace") as inp:
        for number, line in enumerate(inp, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                warnings.append(f"{path}:{number}: invalid/incomplete JSON skipped")
                continue
            if isinstance(record, dict):
                events.append(record)
    return events, warnings


def same_repo(candidate, target):
    if not isinstance(candidate, str) or not candidate.strip():
        return False
    candidate = os.path.expandvars(os.path.expanduser(candidate.strip()))
    # Avoid treating repo slugs or remote URLs as local paths.
    if not os.path.isabs(candidate):
        return False
    path = Path(candidate).resolve(strict=False)
    return path == target or target in path.parents


def repo_match(meta, events, target):
    candidates = [meta.get(k) for k in ('git_root', 'cwd', 'working_directory')]
    for e in events:
        if e.get('type') == 'session.start':
            data = e.get('data') or {}
            ctx = data.get('context') or {}
            if isinstance(ctx, dict):
                candidates += [ctx.get(k) for k in ('git_root', 'gitRoot', 'cwd', 'workingDirectory')]
            candidates += [data.get('cwd')]
    return any(same_repo(c, target) for c in candidates)


def timestamp(event):
    return str(event.get('timestamp') or '')


def content_text(data):
    value = data.get('content')
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, dict) and isinstance(part.get('text'), str):
                parts.append(part['text'])
        return '\n'.join(parts)
    return ''


def fenced(text):
    # Markdown code fence long enough not to conflict with the original prompt.
    longest = max((len(m.group()) for m in re.finditer(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}text\n{text.rstrip()}\n{fence}\n'


def date_or_unknown(ts):
    return ts or 'timestamp unavailable'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path, help='Absolute or relative path to repository root')
    parser.add_argument('--out', required=True, type=Path, help='Output folder (preferably outside repo)')
    parser.add_argument('--copilot-home', type=Path, default=Path(os.environ.get('COPILOT_HOME', '~/.copilot')).expanduser())
    parser.add_argument('--include-subagents', action='store_true', help='Also include internal subagent messages (default: human/root agent only)')
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    if not repo.is_dir():
        parser.error(f'Not a directory: {repo}')
    root = args.copilot_home.expanduser() / 'session-state'
    if not root.is_dir():
        parser.error(f'Copilot sessions not found: {root}')
    output = args.out.expanduser().resolve()
    if output == root or root in output.parents:
        parser.error('Output must not be placed inside Copilot session-state')
    if output.exists() and any(output.iterdir()):
        parser.error(f'Output folder must be empty or not exist: {output}')

    records = []
    warnings = []
    paths = sorted(root.glob('*/events.jsonl')) + sorted(root.glob('*.jsonl'))
    for path in paths:
        events, errors = read_events(path)
        warnings.extend(errors)
        metadata = yaml_fields(path.parent / 'workspace.yaml') if path.name == 'events.jsonl' else {}
        if not repo_match(metadata, events, repo):
            continue
        messages = []
        latest_model = None
        for event in events:
            kind = event.get('type')
            data = event.get('data') or {}
            if not isinstance(data, dict):
                continue
            if kind == 'session.start':
                latest_model = data.get('selectedModel') or latest_model
            elif kind == 'session.model_change':
                latest_model = data.get('model') or data.get('newModel') or data.get('selectedModel') or latest_model
            if kind not in ('user.message', 'assistant.message'):
                continue
            if event.get('agentId') and not args.include_subagents:
                continue
            body = content_text(data)
            if not body.strip():
                continue
            if kind == 'assistant.message':
                latest_model = data.get('model') or latest_model
            messages.append({
                'role': 'USER' if kind == 'user.message' else 'ASSISTANT',
                'timestamp': timestamp(event),
                'model': latest_model if kind == 'assistant.message' else None,
                'agent_id': event.get('agentId'),
                'content': body,
            })
        if not messages:
            continue
        session_id = str(metadata.get('id') or (path.parent.name if path.name == 'events.jsonl' else path.stem))
        records.append({'session_id': session_id, 'source': str(path),
                        'metadata': metadata, 'messages': messages,
                        'first': messages[0]['timestamp']})

    records.sort(key=lambda r: (r['first'], r['session_id']))
    output.mkdir(parents=True, exist_ok=True)
    sessions_dir = output / 'sessions'
    sessions_dir.mkdir()
    prompt_doc = [f'# User prompts for {repo.name}\n', f'Repository: `{repo}`\n']
    conversation_doc = [f'# Copilot conversations for {repo.name}\n', f'Repository: `{repo}`\n']
    manifest = {'repository': str(repo), 'copilot_home': str(args.copilot_home),
                'sessions_scanned': len(paths), 'sessions_matched': len(records),
                'warnings': warnings, 'sessions': []}
    for record in records:
        sid = record['session_id']
        session_header = f'## Session {sid}\n\nSource: `{record["source"]}`\n'
        session_doc = [f'# Copilot session {sid}\n', f'Source: `{record["source"]}`\n']
        conversation_doc.append(session_header)
        pcount = acount = 0
        for msg in record['messages']:
            model = f' | model: {msg["model"]}' if msg['model'] else ''
            agent = f' | subagent: {msg["agent_id"]}' if msg['agent_id'] else ''
            header = f'### {msg["role"]} | {date_or_unknown(msg["timestamp"])}{model}{agent}\n\n'
            fragment = header + fenced(msg['content']) + '\n'
            session_doc.append(fragment)
            conversation_doc.append(fragment)
            if msg['role'] == 'USER':
                pcount += 1
                prompt_doc.append(f'## Session {sid} | {date_or_unknown(msg["timestamp"])}\n\n' + fenced(msg['content']) + '\n')
            else:
                acount += 1
        filename = f'{sid}.md'.replace('/', '_')
        (sessions_dir / filename).write_text('\n'.join(session_doc), encoding='utf-8')
        manifest['sessions'].append({'id': sid, 'source': record['source'], 'file': f'sessions/{filename}',
                                     'first_message': record['first'], 'user_prompts': pcount, 'assistant_messages': acount,
                                     'workspace': record['metadata']})
    (output / 'prompts-only.md').write_text('\n'.join(prompt_doc), encoding='utf-8')
    (output / 'conversation.md').write_text('\n'.join(conversation_doc), encoding='utf-8')
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Scanned {len(paths)} transcript files; exported {len(records)} sessions, '
          f'{sum(x["user_prompts"] for x in manifest["sessions"])} prompts, '
          f'{sum(x["assistant_messages"] for x in manifest["sessions"])} assistant messages.')
    print(f'Export: {output}')
    if warnings:
        print(f'Warning: {len(warnings)} unreadable JSONL lines. See manifest.json.', file=sys.stderr)
    if not records:
        print('No matching sessions. Confirm --repo matches cwd/git_root stored when sessions started.', file=sys.stderr)


if __name__ == '__main__':
    main()
