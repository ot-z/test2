"""洛谷公开题面批量下载（命令行）。

示例：
    # 抓 P1001~P1020（默认从 P1000 起，可按 --start/--end 调整）
    .venv\\Scripts\\python.exe scripts\\fetch_luogu.py --start 1001 --end 1020

    # 只给题库里已录入的洛谷题补题面 / 标签 / 知识点 / 难度
    .venv\\Scripts\\python.exe scripts\\fetch_luogu.py --existing

    # 全量：先拉公开题库清单，再按清单逐题抓题面（首次会多花几分钟拉清单）
    .venv\\Scripts\\python.exe scripts\\fetch_luogu.py --all

    # 断点续跑：直接再运行同一条命令即可，已抓过的题号自动跳过
    # 仅预览不联网：加 --dry-run

所有外部请求默认限速 >= 1.2 秒；随时 Ctrl+C 中断，进度保存在
data/luogu_fetch_state.json，题面 Markdown 在 exports/luogu/，索引是 index.csv。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.luogu_store import fetch_batch, rebuild_index, status  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="洛谷公开题面批量下载并写入本地题库")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--existing", action="store_true", help="只抓题库中已录入的洛谷题")
    mode.add_argument("--catalog", action="store_true", help="按公开题库清单抓取（精确题号）")
    mode.add_argument("--all", action="store_true", help="--catalog 的别名：全量抓取")
    p.add_argument("--start", type=int, default=1000, help="起始题号数字，默认 1000")
    p.add_argument("--end", type=int, default=None, help="结束题号数字；不填则由 --count 决定")
    p.add_argument("--count", type=int, default=20, help="未指定 --end 时抓取的连续题号数，默认 20")
    p.add_argument("--pid-min", type=int, default=None, help="清单/已录题模式下的最小题号")
    p.add_argument("--pid-max", type=int, default=None, help="清单/已录题模式下的最大题号")
    p.add_argument("--max-items", type=int, default=None, help="本次最多处理多少题（分批用）")
    p.add_argument("--interval", type=float, default=None, help="请求间隔秒数，最低 0.8，默认取配置值")
    p.add_argument("--max-missing", type=int, default=None, help="连续多少个不存在题号后停止")
    p.add_argument("--dry-run", action="store_true", help="只列出将要抓取的题号，不联网不写库")
    p.add_argument("--refresh", action="store_true", help="忽略进度，从头重抓本次范围")
    p.add_argument("--refresh-catalog", action="store_true", help="重新拉取公开题库清单")
    p.add_argument("--refresh-tags", action="store_true", help="重新拉取标签字典")
    p.add_argument("--retry-missing", action="store_true", help="重试之前记录为「不存在」的题号")
    p.add_argument("--force", action="store_true", help="人工编辑过的题目也覆盖元信息")
    p.add_argument("--no-files", action="store_true", help="只写数据库，不导出 Markdown 文件")
    p.add_argument("--only-status", action="store_true", help="只查看当前采集进度，不联网")
    p.add_argument("--rebuild-index", action="store_true",
                   help="只根据题库快照重建 index.csv（不联网、不抓题；常用于关闭占用文件后补齐索引）")
    return p


def main() -> int:
    args = build_parser().parse_args()
    db = SessionLocal()
    try:
        if args.rebuild_index:
            info = rebuild_index(db)
            print(f"索引重建：{info['path'] or '（写入失败）'}（{info['rows']} 行）")
            if info.get("warning"):
                print(f"  ! {info['warning']}")
            return 0
        if args.only_status:
            info = status(db)
            print("=== 洛谷题面采集进度 ===")
            print(f"题库中的洛谷题目：{info['problems_luogu']} 题")
            print(f"题面快照：{info['snapshots']} 份；映射知识点关联：{info['mapped_links']} 条")
            print(f"已完成题号：{info['state']['done']} 个；最后处理：{info['state']['last_pid'] or '—'}")
            print(f"不存在：{info['state']['missing']} 个；失败：{info['state']['failed']} 个")
            print(f"题库清单：{info['catalog']['pids']} 个题号（{info['catalog']['updated_at'] or '未拉取'}）")
            print(f"题面目录：{info['md_dir']}")
            print(f"索引文件：{info['index_path'] or '（尚未生成）'}")
            print(f"进度文件：{info['state_path']}")
            return 0

        mode = "catalog" if (args.all or args.catalog) else ("existing" if args.existing else "range")
        params = {
            "mode": mode,
            "start": args.start,
            "end": args.end,
            "count": args.count,
            "pid_min": args.pid_min,
            "pid_max": args.pid_max,
            "max_items": args.max_items,
            "interval": args.interval,
            "max_missing": args.max_missing,
            "dry_run": args.dry_run,
            "refresh": args.refresh,
            "refresh_catalog": args.refresh_catalog,
            "refresh_tags": args.refresh_tags,
            "skip_missing": not args.retry_missing,
            "force": args.force,
            "save_files": not args.no_files,
        }

        def progress(event: dict) -> None:
            kind = event.get("event")
            if kind == "log":
                print(f"  · {event.get('message')}", flush=True)
            elif kind == "missing":
                print(f"  - {event['pid']} 不存在或未公开", flush=True)
            elif kind == "error":
                print(f"  ! {event.get('pid', '')} {event.get('message')}", flush=True)
            elif kind == "item":
                r = event
                print(f"  + {r['pid']} {r['title']} | {r['difficulty']} | "
                      f"标签 {','.join(r['tags'][:4]) or '—'} | "
                      f"知识点 {','.join(r['knowledge_nodes'][:5]) or '—'} | "
                      f"快照 {r['snapshot']}", flush=True)

        print(f"模式：{mode}｜范围：{args.start} ~ {args.end or (args.start + args.count - 1)}"
              f"｜限速：{args.interval or '配置默认'} 秒/请求", flush=True)
        result = fetch_batch(db, params, progress=progress)
        if result.get("dry_run"):
            print(f"预览：将处理 {result['planned']} 个题号，前 50 个：{', '.join(result['pids'])}")
            return 0
        print("\n=== 本次结果 ===")
        print(result["message"])
        print(f"新增 {result['added']}｜更新 {result['updated']}｜人工编辑跳过 {result['skipped_manual']}"
              f"｜已抓跳过 {result['skipped']}｜快照 {result['snapshots']}｜文件 {result['files']}"
              f"｜不存在 {result['missing']}｜失败 {result['failed']}")
        idx = result["index"]
        print(f"索引：{idx['path'] or '（写入失败）'}（{idx['rows']} 行）")
        if idx.get("warning"):
            print(f"  ! {idx['warning']}")
        print(f"题面目录：{ROOT / 'exports' / 'luogu'}")
        print(f"进度：{result['state_path']}（再次运行同一命令可续跑）")
        return 0
    except KeyboardInterrupt:
        print("\n已中断。进度会在下次运行时自动续传。", flush=True)
        return 130
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
