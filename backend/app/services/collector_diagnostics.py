from __future__ import annotations

import argparse
import gc
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_PI_DATA_ROOT = Path('/mnt/nvme/autoflip-data')
APP_DIR = Path(__file__).resolve().parents[1]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def today_suffix() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%d')


def get_data_root() -> Path:
    env_root = os.getenv('OSRS_FLIP_DATA_ROOT')
    if env_root:
        return Path(env_root).expanduser().resolve()
    if DEFAULT_PI_DATA_ROOT.exists():
        return DEFAULT_PI_DATA_ROOT
    return (APP_DIR / 'data').resolve()


def diagnostics_root() -> Path:
    root = get_data_root() / 'logs' / 'collector_diagnostics'
    root.mkdir(parents=True, exist_ok=True)
    return root


def daily_log_name(base_filename: str) -> str:
    if base_filename.endswith('.jsonl'):
        return f"{base_filename[:-6]}_{today_suffix()}.jsonl"
    return base_filename


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors='replace')
    except Exception:
        return ''


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _rss_kb_for_pid(pid: int) -> int | None:
    status = _read_text(Path('/proc') / str(pid) / 'status')
    for line in status.splitlines():
        if line.startswith('VmRSS:'):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def current_process_memory() -> dict[str, Any]:
    pid = os.getpid()
    rss_kb = _rss_kb_for_pid(pid)
    return {
        'pid': pid,
        'rss_kb': rss_kb,
        'rss_mb': round(rss_kb / 1024, 2) if rss_kb is not None else None,
    }


def find_collector_processes() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    proc = Path('/proc')
    if not proc.exists():
        return found

    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmdline_path = entry / 'cmdline'
        try:
            raw = cmdline_path.read_bytes()
        except Exception:
            continue
        cmdline = raw.replace(b'\x00', b' ').decode(errors='replace').strip()
        if not cmdline:
            continue
        if 'collector_runner' not in cmdline and 'app.services.collector_runner' not in cmdline:
            continue
        rss_kb = _rss_kb_for_pid(pid)
        found.append({
            'pid': pid,
            'rss_kb': rss_kb,
            'rss_mb': round(rss_kb / 1024, 2) if rss_kb is not None else None,
            'cmdline': cmdline[:500],
        })

    found.sort(key=lambda row: row.get('rss_kb') or 0, reverse=True)
    return found


def _file_meta(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
    except FileNotFoundError:
        return {'path': str(path), 'exists': False}
    except Exception as exc:
        return {'path': str(path), 'exists': False, 'error': str(exc)}
    return {
        'path': str(path),
        'exists': True,
        'size_bytes': st.st_size,
        'size_mb': round(st.st_size / (1024 * 1024), 3),
        'mtime': datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
    }


def cache_provenance_snapshot(cache_path: Path) -> dict[str, Any]:
    payload = _read_json_file(cache_path, {})
    if not isinstance(payload, dict):
        return {'exists': cache_path.exists(), 'available': False, 'reason': 'cache is not a JSON object'}
    provenance = payload.get('cache_provenance')
    storage = payload.get('storage') if isinstance(payload.get('storage'), dict) else {}
    if not isinstance(provenance, dict):
        return {
            'exists': cache_path.exists(),
            'available': False,
            'updated_at': payload.get('updated_at'),
            'snapshot_bucket': payload.get('snapshot_bucket'),
            'cache_build_mode': storage.get('cache_build_mode'),
            'reason': 'cache_provenance missing; rebuild market_cache with provenance patch',
        }
    sources = provenance.get('sources') if isinstance(provenance.get('sources'), dict) else {}
    archive = sources.get('archive_month') if isinstance(sources.get('archive_month'), dict) else {}
    snapshots = sources.get('snapshots') if isinstance(sources.get('snapshots'), dict) else {}
    return {
        'exists': cache_path.exists(),
        'available': True,
        'updated_at': payload.get('updated_at'),
        'snapshot_bucket': payload.get('snapshot_bucket'),
        'cache_build_mode': storage.get('cache_build_mode'),
        'archive_month_used': bool(provenance.get('archive_month_used')),
        'archive_month_files_considered': archive.get('files_considered'),
        'archive_month_rows_seen': archive.get('rows_seen'),
        'archive_month_rows_used': archive.get('rows_used'),
        'archive_month_oldest_ts_used': archive.get('oldest_ts_used'),
        'archive_month_newest_ts_used': archive.get('newest_ts_used'),
        'snapshot_files_considered': snapshots.get('files_considered'),
        'snapshot_rows_seen': snapshots.get('rows_seen'),
        'snapshot_rows_used': snapshots.get('rows_used'),
        'snapshot_oldest_ts_used': snapshots.get('oldest_ts_used'),
        'snapshot_newest_ts_used': snapshots.get('newest_ts_used'),
        'tracked_item_count': provenance.get('tracked_item_count'),
        'items_with_history_stats': provenance.get('items_with_history_stats'),
    }


def storage_snapshot() -> dict[str, Any]:
    root = get_data_root()
    cache_path = root / 'cache' / 'market_cache.json'
    watched = [
        cache_path,
        root / 'market_history' / 'current_snapshot.json',
        root / 'market_history' / 'tracked_items.json',
    ]

    history_root = root / 'market_history'
    sizes: dict[str, Any] = {}
    for name in ['raw', 'archive', 'events', 'snapshots']:
        folder = history_root / name
        total = 0
        count = 0
        newest = None
        if folder.exists():
            for f in folder.rglob('*'):
                if not f.is_file():
                    continue
                try:
                    st = f.stat()
                except Exception:
                    continue
                total += st.st_size
                count += 1
                newest = max(newest or st.st_mtime, st.st_mtime)
        sizes[name] = {
            'path': str(folder),
            'file_count': count,
            'size_mb': round(total / (1024 * 1024), 3),
            'newest_mtime': datetime.fromtimestamp(newest, UTC).isoformat() if newest else None,
        }

    return {
        'data_root': str(root),
        'files': {path.name: _file_meta(path) for path in watched},
        'history_dirs': sizes,
        'cache_provenance': cache_provenance_snapshot(cache_path),
    }


def checkpoint(label: str, extra: dict[str, Any] | None = None, persist: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'ts': utc_now_iso(),
        'label': label,
        'process': current_process_memory(),
        'storage': storage_snapshot(),
        'gc_counts': list(gc.get_count()),
    }
    if extra:
        payload['extra'] = extra
    if persist:
        write_record('collector_runner_checkpoints.jsonl', payload)
    return payload


def write_record(filename: str, payload: dict[str, Any]) -> None:
    root = diagnostics_root()
    path = root / daily_log_name(filename)
    line = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    with path.open('a', encoding='utf-8') as f:
        f.write(line + '\n')
    latest = root / 'latest.json'
    latest.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')


def scan_once() -> dict[str, Any]:
    payload = {
        'ts': utc_now_iso(),
        'collector_processes': find_collector_processes(),
        'scanner_process': current_process_memory(),
        'storage': storage_snapshot(),
    }
    write_record('scanner.jsonl', payload)
    return payload


def human_summary(payload: dict[str, Any]) -> str:
    lines = [f"collector diagnostic scanner ts={payload.get('ts')}"]
    procs = payload.get('collector_processes') or []
    if not procs:
        lines.append('collector_runner: not found')
    else:
        for proc in procs[:5]:
            lines.append(f"collector_runner pid={proc.get('pid')} rss_mb={proc.get('rss_mb')} cmd={proc.get('cmdline')}")
    storage = payload.get('storage') or {}
    lines.append(f"data_root={storage.get('data_root')}")
    for name, meta in (storage.get('files') or {}).items():
        lines.append(f"file {name}: exists={meta.get('exists')} size_mb={meta.get('size_mb')} mtime={meta.get('mtime')}")
    for name, meta in (storage.get('history_dirs') or {}).items():
        lines.append(f"history {name}: files={meta.get('file_count')} size_mb={meta.get('size_mb')} newest={meta.get('newest_mtime')}")
    provenance = (storage.get('cache_provenance') or {})
    lines.append(
        'cache provenance: '
        f"available={provenance.get('available')} "
        f"mode={provenance.get('cache_build_mode')} "
        f"archive_month_used={provenance.get('archive_month_used')} "
        f"archive_rows_used={provenance.get('archive_month_rows_used')} "
        f"archive_files={provenance.get('archive_month_files_considered')} "
        f"oldest_archive_ts={provenance.get('archive_month_oldest_ts_used')} "
        f"newest_archive_ts={provenance.get('archive_month_newest_ts_used')}"
    )
    if provenance.get('available') is False and provenance.get('reason'):
        lines.append(f"cache provenance note: {provenance.get('reason')}")
    return '\n'.join(lines) + '\n'


def scan_loop(interval_seconds: int) -> None:
    root = diagnostics_root()
    print(f"[collector_diagnostics] scanner starting interval={interval_seconds}s root={root}", flush=True)
    while True:
        payload = scan_once()
        summary = human_summary(payload)
        (root / 'latest.log').write_text(summary, encoding='utf-8')
        print(summary.strip(), flush=True)
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description='Collector memory/storage diagnostic helper')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('scan-once')
    loop = sub.add_parser('scan-loop')
    loop.add_argument('--interval-seconds', type=int, default=int(os.getenv('OSRS_COLLECTOR_DIAG_INTERVAL_SECONDS', '30')))
    args = parser.parse_args()

    if args.command == 'scan-once':
        payload = scan_once()
        print(human_summary(payload), end='')
        return
    if args.command == 'scan-loop':
        scan_loop(max(5, args.interval_seconds))
        return
    parser.print_help()


if __name__ == '__main__':
    main()
