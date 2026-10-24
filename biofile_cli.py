#!/usr/bin/env python3
"""
biofile-cli: High-speed NFS File Index Search CLI & MCP Server
Compatible with ACTGenomics biofile-manager index.db (SQLite + FTS5).
Provides sub-second file search across tens of millions of records on multi-terabyte NFS shares.
"""

import os
import sys
import json
import time
import sqlite3
import argparse
import re
from typing import List, Dict, Any, Optional

DEFAULT_DB_PATH = os.environ.get("INDEX_DB_PATH", "/srv/biofile-manager/data/index.db")
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file not found: {db_path}")
    # SQLite WAL read-only mode to prevent write locking
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def format_bytes(b: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024.0:
            return f"{b:3.1f} {unit}"
        b /= 1024.0
    return f"{b:.1f} PB"


def format_time(ts: int) -> str:
    if ts <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def query_files(
    conn: sqlite3.Connection,
    query: Optional[str] = None,
    barcode: Optional[str] = None,
    ext: Optional[str] = None,
    root: Optional[str] = None,
    parent: Optional[str] = None,
    min_size: Optional[int] = None,
    max_size: Optional[int] = None,
    after: Optional[int] = None,
    before: Optional[int] = None,
    kind: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0
) -> Dict[str, Any]:
    t0 = time.time()
    cur = conn.cursor()
    limit = max(1, min(limit, MAX_LIMIT))

    where_clauses: List[str] = []
    params: List[Any] = []
    uses_fts = False

    if barcode:
        barcode_clean = barcode.strip().replace("'", "").replace('"', '')
        if "*" in barcode_clean or "?" in barcode_clean:
            like_val = barcode_clean.replace("*", "%").replace("?", "_")
            where_clauses.append("f.barcode LIKE ?")
            params.append(like_val)
        else:
            where_clauses.append("f.barcode = ?")
            params.append(barcode_clean)

    if query:
        q = query.strip()
        if any(c in q for c in ['*', '?']):
            like_val = q.replace("*", "%").replace("?", "_")
            where_clauses.append("f.name LIKE ?")
            params.append(like_val)
        else:
            clean_term = re.sub(r'[^\w\s\.\-]', ' ', q).strip()
            if clean_term:
                tokens = clean_term.split()
                fts_expr = " ".join([f'"{tok}"*' for tok in tokens])
                uses_fts = True

    if ext:
        ext_clean = ext.strip().lower().lstrip(".")
        where_clauses.append("f.ext = ?")
        params.append(ext_clean)

    if root:
        where_clauses.append("f.root = ?")
        params.append(root.strip())

    if parent:
        parent_clean = parent.rstrip("/")
        where_clauses.append("f.parent = ?")
        params.append(parent_clean)

    if min_size is not None:
        where_clauses.append("f.size >= ?")
        params.append(min_size)

    if max_size is not None:
        where_clauses.append("f.size <= ?")
        params.append(max_size)

    if after is not None:
        where_clauses.append("f.mtime >= ?")
        params.append(after)

    if before is not None:
        where_clauses.append("f.mtime <= ?")
        params.append(before)

    if kind == "file":
        where_clauses.append("f.is_dir = 0")
    elif kind == "dir":
        where_clauses.append("f.is_dir = 1")

    if uses_fts:
        fts_where = "fts.files_fts MATCH ?"
        all_where = [fts_where] + where_clauses
        where_str = " WHERE " + " AND ".join(all_where)
        sql = f"""
            SELECT f.id, f.root, f.path, f.parent, f.name, f.ext, f.is_dir, f.size, f.mtime, f.barcode
            FROM files_fts fts
            JOIN files f ON fts.rowid = f.id
            {where_str}
            ORDER BY f.mtime DESC
            LIMIT ? OFFSET ?
        """
        all_params = [fts_expr] + params + [limit, offset]
    else:
        where_str = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        sql = f"""
            SELECT f.id, f.root, f.path, f.parent, f.name, f.ext, f.is_dir, f.size, f.mtime, f.barcode
            FROM files f
            {where_str}
            ORDER BY f.mtime DESC
            LIMIT ? OFFSET ?
        """
        all_params = params + [limit, offset]

    cur.execute(sql, all_params)
    rows = cur.fetchall()
    duration_ms = (time.time() - t0) * 1000

    results = []
    for r in rows:
        results.append({
            "path": r["path"],
            "name": r["name"],
            "is_dir": bool(r["is_dir"]),
            "size": r["size"],
            "size_human": format_bytes(r["size"]) if not r["is_dir"] else "<DIR>",
            "mtime": r["mtime"],
            "mtime_human": format_time(r["mtime"]),
            "barcode": r["barcode"],
            "ext": r["ext"],
            "root": r["root"]
        })

    return {
        "count": len(results),
        "duration_ms": round(duration_ms, 2),
        "results": results
    }


def list_folder_contents(conn: sqlite3.Connection, folder_path: str, limit: int = 100) -> Dict[str, Any]:
    t0 = time.time()
    clean_path = folder_path.rstrip("/")
    cur = conn.cursor()
    cur.execute("""
        SELECT root, path, name, is_dir, size, mtime, barcode, ext
        FROM files
        WHERE parent = ?
        ORDER BY is_dir DESC, name ASC
        LIMIT ?
    """, (clean_path, limit))
    rows = cur.fetchall()
    duration_ms = (time.time() - t0) * 1000

    results = []
    for r in rows:
        results.append({
            "path": r["path"],
            "name": r["name"],
            "is_dir": bool(r["is_dir"]),
            "size": r["size"],
            "size_human": format_bytes(r["size"]) if not r["is_dir"] else "<DIR>",
            "mtime": r["mtime"],
            "mtime_human": format_time(r["mtime"]),
            "barcode": r["barcode"],
            "ext": r["ext"]
        })

    return {
        "folder": clean_path,
        "count": len(results),
        "duration_ms": round(duration_ms, 2),
        "results": results
    }


def get_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM files")
    total_files = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM dirs")
    total_dirs = cur.fetchone()[0]

    cur.execute("SELECT DISTINCT root FROM files ORDER BY root")
    roots = [r[0] for r in cur.fetchall()]

    cur.execute("""
        SELECT root, kind, status, entries, errors, finished_at
        FROM scans
        ORDER BY id DESC
        LIMIT 10
    """)
    recent_scans = []
    for r in cur.fetchall():
        recent_scans.append({
            "root": r[0],
            "kind": r[1],
            "status": r[2],
            "entries": r[3],
            "errors": r[4],
            "finished_at": format_time(r[5] // 1000 if r[5] > 1e11 else r[5])
        })

    return {
        "total_files": total_files,
        "total_dirs": total_dirs,
        "managed_roots": roots,
        "recent_scans": recent_scans
    }


def read_snippet(path: str, lines: int = 30, mode: str = "head") -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}
    if os.path.isdir(path):
        return {"error": f"Path is a directory: {path}"}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            selected = all_lines[-lines:] if mode == "tail" and len(all_lines) >= lines else all_lines[:lines]
            return {
                "path": path,
                "mode": mode,
                "lines_returned": len(selected),
                "total_lines": len(all_lines),
                "content": "".join(selected)
            }
    except Exception as e:
        return {"error": f"Failed to read file: {str(e)}"}


def run_mcp_server(db_path: str = DEFAULT_DB_PATH):
    conn = get_db_connection(db_path)

    tools_spec = [
        {
            "name": "search_files",
            "description": "High-speed multi-criteria file search on indexed NFS shares (supports full-text, globs, extensions, size, dates).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword or glob pattern (e.g. '*R1*.fastq.gz' or 'AANB01')"},
                    "barcode": {"type": "string", "description": "Specific sample barcode (e.g. 'AANB01_513')"},
                    "ext": {"type": "string", "description": "File extension without dot (e.g. 'bam', 'fastq.gz', 'vcf.gz')"},
                    "root": {"type": "string", "description": "Limit search to a specific NFS mount root"},
                    "parent": {"type": "string", "description": "Limit search to immediate children of this directory"},
                    "min_size_mb": {"type": "number", "description": "Minimum file size in megabytes"},
                    "max_size_mb": {"type": "number", "description": "Maximum file size in megabytes"},
                    "kind": {"type": "string", "enum": ["file", "dir"], "description": "Filter by file or directory"},
                    "limit": {"type": "integer", "description": "Max results to return (default 50, max 500)"}
                }
            }
        },
        {
            "name": "find_sample_files",
            "description": "Quick bioinformatics shortcut to locate all analysis output files (FASTQ, BAM, VCF, reports) for a specific sample barcode.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "barcode": {"type": "string", "description": "Sample barcode (e.g. 'AANB01_513' or 'AANB01*')"},
                    "ext": {"type": "string", "description": "Optional extension filter (e.g. 'fastq.gz', 'bam', 'vcf.gz')"},
                    "limit": {"type": "integer", "description": "Max results (default 50)"}
                },
                "required": ["barcode"]
            }
        },
        {
            "name": "list_folder",
            "description": "Instant virtual directory listing from database index without touching slow NFS storage.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folder_path": {"type": "string", "description": "Absolute path of directory to list"},
                    "limit": {"type": "integer", "description": "Max entries (default 100)"}
                },
                "required": ["folder_path"]
            }
        },
        {
            "name": "read_snippet",
            "description": "Read head or tail lines of a text report, log, or QC output on the index host.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path on host"},
                    "lines": {"type": "integer", "description": "Number of lines (default 30)"},
                    "mode": {"type": "string", "enum": ["head", "tail"], "description": "Read from start (head) or end (tail)"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "get_index_status",
            "description": "Get current health, record counts, managed NFS shares, and recent crawl scans.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        }
    ]

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "biofile-cli-mcp", "version": "1.0.0"}
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tools_spec}
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            try:
                if tool_name == "search_files":
                    min_bytes = int(args["min_size_mb"] * 1024 * 1024) if "min_size_mb" in args else None
                    max_bytes = int(args["max_size_mb"] * 1024 * 1024) if "max_size_mb" in args else None
                    data = query_files(
                        conn,
                        query=args.get("query"),
                        barcode=args.get("barcode"),
                        ext=args.get("ext"),
                        root=args.get("root"),
                        parent=args.get("parent"),
                        min_size=min_bytes,
                        max_size=max_bytes,
                        kind=args.get("kind"),
                        limit=args.get("limit", DEFAULT_LIMIT)
                    )
                elif tool_name == "find_sample_files":
                    data = query_files(
                        conn,
                        barcode=args.get("barcode"),
                        ext=args.get("ext"),
                        limit=args.get("limit", DEFAULT_LIMIT)
                    )
                elif tool_name == "list_folder":
                    data = list_folder_contents(conn, args.get("folder_path", ""), args.get("limit", 100))
                elif tool_name == "read_snippet":
                    data = read_snippet(args.get("path", ""), args.get("lines", 30), args.get("mode", "head"))
                elif tool_name == "get_index_status":
                    data = get_status(conn)
                else:
                    data = {"error": f"Unknown tool: {tool_name}"}

                content_text = json.dumps(data, indent=2, ensure_ascii=False)
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": content_text}]
                    }
                }
            except Exception as e:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": f"Execution error: {str(e)}"}]
                    }
                }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "ping":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {}}) + "\n")
            sys.stdout.flush()

        else:
            if req_id is not None:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(
        prog="biofile-cli",
        description="ACTGenomics High-Speed NFS File Index Search CLI & MCP Server"
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"Path to index.db (default: {DEFAULT_DB_PATH})")

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # search
    p_search = subparsers.add_parser("search", help="Search files by keyword, glob, extension, size, or date")
    p_search.add_argument("query", nargs="?", default=None, help="Keyword or glob pattern (*, ?)")
    p_search.add_argument("--barcode", "-b", help="Sample barcode (e.g. AANB01_513)")
    p_search.add_argument("--ext", "-e", help="Extension without dot (e.g. fastq.gz, bam, vcf.gz)")
    p_search.add_argument("--root", help="Limit search to this NFS mount root")
    p_search.add_argument("--parent", help="Limit search to immediate children of this folder")
    p_search.add_argument("--min-size", help="Min size (e.g. 100K, 10M, 1G)")
    p_search.add_argument("--max-size", help="Max size (e.g. 500M, 5G)")
    p_search.add_argument("--kind", choices=["file", "dir"], help="Filter by file or dir")
    p_search.add_argument("-n", "--limit", type=int, default=DEFAULT_LIMIT, help="Max results")
    p_search.add_argument("-1", "--path-only", action="store_true", help="Print only paths (pipeline-friendly)")
    p_search.add_argument("--json", action="store_true", help="Output raw JSON")

    # sample
    p_sample = subparsers.add_parser("sample", help="Bioinformatics shortcut: find all files for a sample barcode")
    p_sample.add_argument("barcode", help="Sample barcode (e.g. AANB01_513)")
    p_sample.add_argument("--ext", "-e", help="Extension filter (e.g. fastq.gz, bam)")
    p_sample.add_argument("-n", "--limit", type=int, default=DEFAULT_LIMIT, help="Max results")
    p_sample.add_argument("-1", "--path-only", action="store_true", help="Print only paths")
    p_sample.add_argument("--json", action="store_true", help="Output raw JSON")

    # ls
    p_ls = subparsers.add_parser("ls", help="Virtual directory listing from index without accessing NFS")
    p_ls.add_argument("folder", help="Absolute directory path")
    p_ls.add_argument("-n", "--limit", type=int, default=100, help="Max items")
    p_ls.add_argument("-1", "--path-only", action="store_true", help="Print only paths")
    p_ls.add_argument("--json", action="store_true", help="Output raw JSON")

    # read
    p_read = subparsers.add_parser("read", help="Read head/tail lines of a file")
    p_read.add_argument("path", help="Absolute file path")
    p_read.add_argument("-n", "--lines", type=int, default=30, help="Number of lines")
    p_read.add_argument("--tail", action="store_true", help="Read from end instead of beginning")

    # status
    p_status = subparsers.add_parser("status", help="Show index health, record counts, and crawl status")
    p_status.add_argument("--json", action="store_true", help="Output raw JSON")

    # mcp
    subparsers.add_parser("mcp", help="Run in MCP (Model Context Protocol) stdio server mode")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "mcp":
        run_mcp_server(args.db)
        return

    try:
        conn = get_db_connection(args.db)
    except Exception as e:
        sys.stderr.write(f"Error opening index database: {e}\n")
        sys.exit(1)

    def parse_size_str(s: Optional[str]) -> Optional[int]:
        if not s:
            return None
        m = re.match(r'^(\d+(?:\.\d+)?)\s*([KMGTP]?B?)$', s.strip().upper())
        if not m:
            raise ValueError(f"Invalid size string: {s}")
        val, unit = float(m.group(1)), m.group(2)
        mult = 1
        if 'K' in unit: mult = 1024
        elif 'M' in unit: mult = 1024**2
        elif 'G' in unit: mult = 1024**3
        elif 'T' in unit: mult = 1024**4
        return int(val * mult)

    if args.command == "search":
        min_bytes = parse_size_str(args.min_size)
        max_bytes = parse_size_str(args.max_size)
        res = query_files(
            conn,
            query=args.query,
            barcode=args.barcode,
            ext=args.ext,
            root=args.root,
            parent=args.parent,
            min_size=min_bytes,
            max_size=max_bytes,
            kind=args.kind,
            limit=args.limit
        )
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        elif args.path_only:
            for item in res["results"]:
                print(item["path"])
        else:
            print(f"\nFound {res['count']} matches in {res['duration_ms']} ms:")
            print("-" * 100)
            for item in res["results"]:
                type_flag = "[DIR ]" if item["is_dir"] else "[FILE]"
                size_str = f"{item['size_human']:>10}"
                mtime_str = item["mtime_human"]
                bc_str = f"({item['barcode']})" if item["barcode"] else ""
                print(f"{type_flag} {size_str}  {mtime_str}  {item['path']} {bc_str}")
            print("-" * 100)

    elif args.command == "sample":
        res = query_files(conn, barcode=args.barcode, ext=args.ext, limit=args.limit)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        elif args.path_only:
            for item in res["results"]:
                print(item["path"])
        else:
            print(f"\nSample files for barcode '{args.barcode}' ({res['count']} found in {res['duration_ms']} ms):")
            print("-" * 100)
            for item in res["results"]:
                type_flag = "[DIR ]" if item["is_dir"] else "[FILE]"
                size_str = f"{item['size_human']:>10}"
                mtime_str = item["mtime_human"]
                print(f"{type_flag} {size_str}  {mtime_str}  {item['path']}")
            print("-" * 100)

    elif args.command == "ls":
        res = list_folder_contents(conn, args.folder, limit=args.limit)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        elif args.path_only:
            for item in res["results"]:
                print(item["path"])
        else:
            print(f"\nVirtual listing of {res['folder']} ({res['count']} entries in {res['duration_ms']} ms):")
            print("-" * 100)
            for item in res["results"]:
                type_flag = "[DIR ]" if item["is_dir"] else "[FILE]"
                size_str = f"{item['size_human']:>10}"
                mtime_str = item["mtime_human"]
                print(f"{type_flag} {size_str}  {mtime_str}  {item['name']}")
            print("-" * 100)

    elif args.command == "read":
        mode = "tail" if args.tail else "head"
        res = read_snippet(args.path, lines=args.lines, mode=mode)
        if "error" in res:
            sys.stderr.write(f"Error: {res['error']}\n")
            sys.exit(1)
        sys.stdout.write(res["content"])

    elif args.command == "status":
        res = get_status(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print("\n=== Biofile Index Status ===")
            print(f"Total Indexed Files : {res['total_files']:,}")
            print(f"Total Indexed Dirs  : {res['total_dirs']:,}")
            print(f"Database Path       : {args.db}")
            print(f"\nManaged Roots ({len(res['managed_roots'])} shares):")
            for r in res["managed_roots"]:
                print(f"  - {r}")
            print(f"\nRecent Scans:")
            for s in res["recent_scans"]:
                print(f"  [{s['status'].upper():<7}] {s['kind']:<11} entries={s['entries']:<7} errors={s['errors']:<4} {s['finished_at']} ({s['root']})")
            print("============================\n")


if __name__ == "__main__":
    main()
