#!/usr/bin/env python3
"""lowcc — list functions with high cyclomatic complexity.

Thin wrapper around `lizard` (https://github.com/terryyin/lizard). Lizard does
the parsing and per-function CC calculation; this script filters and emits a
markdown report of functions at or above a configurable CC threshold.

Requires `lizard` to be installed and available on PATH.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


LIZARD_INSTALL_HINT = """\
lowcc requires `lizard` to be installed and available on PATH.

Install options (no root required):
  # uv (recommended — single-binary Python tool manager):
  curl -LsSf https://astral.sh/uv/install.sh | sh
  uv tool install lizard

  # pipx (Debian/Ubuntu/macOS):
  sudo apt install pipx        # or: brew install pipx
  pipx install lizard

  # plain pip into a venv:
  python3 -m venv ~/.venvs/lizard && ~/.venvs/lizard/bin/pip install lizard
  ln -s ~/.venvs/lizard/bin/lizard ~/.local/bin/lizard

After install, verify with: lizard --version
"""


# Extension -> friendly language name (only for the report column).
# Lizard decides what is parseable; this map is used purely for display.
EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".cs": "C#",
    ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala", ".sc": "Scala",
    ".dart": "Dart",
    ".lua": "Lua",
    ".pl": "Perl", ".pm": "Perl",
    ".m": "Objective-C", ".mm": "Objective-C++",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".hpp": "C++", ".hh": "C++", ".hxx": "C++",
    ".sol": "Solidity",
    ".tnsl": "TNSL",
    ".ttcn": "TTCN-3", ".ttcn3": "TTCN-3",
    ".f90": "Fortran", ".f95": "Fortran", ".f03": "Fortran",
    ".erl": "Erlang",
    ".zig": "Zig",
}


# Directories to skip by default. Passed to lizard as -x glob patterns.
DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
    "*/.git/*", "*/.hg/*", "*/.svn/*",
    "*/node_modules/*", "*/bower_components/*", "*/vendor/*",
    "*/__pycache__/*", "*/.venv/*", "*/venv/*", "*/env/*",
    "*/.tox/*", "*/.pytest_cache/*", "*/.mypy_cache/*",
    "*/dist/*", "*/build/*", "*/out/*", "*/target/*", "*/bin/*", "*/obj/*",
    "*/.next/*", "*/.nuxt/*", "*/.cache/*", "*/coverage/*",
    "*/.idea/*", "*/.vscode/*", "*/.vs/*", "*/.gradle/*",
)


@dataclass
class FunctionResult:
    file: str
    language: str
    name: str
    complexity: int
    nloc: int
    params: int
    length: int
    start_line: int


def require_lizard() -> str:
    found = shutil.which("lizard")
    if found is None:
        sys.stderr.write("error: lizard executable not found on PATH.\n\n")
        sys.stderr.write(LIZARD_INSTALL_HINT)
        sys.exit(2)
    return found


def run_lizard(lizard: str, target: Path, excludes: list[str]) -> str:
    """Run lizard in CSV mode and return its stdout."""
    cmd: list[str] = [lizard, "--csv"]
    for pat in excludes:
        cmd.extend(["-x", pat])
    cmd.append(str(target))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    # Lizard returns non-zero when warnings are emitted; still produces CSV.
    # Only treat as failure if there is no stdout at all.
    if not proc.stdout:
        sys.stderr.write("error: lizard produced no output.\n")
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        sys.exit(proc.returncode or 1)
    return proc.stdout


def parse_csv(csv_text: str) -> list[FunctionResult]:
    """Parse lizard's per-function CSV rows.

    Lizard CSV columns: NLOC, CCN, token, PARAM, length, location, file,
    function, long_name, start_line, end_line.
    """
    results: list[FunctionResult] = []
    reader = csv.reader(io.StringIO(csv_text))
    for row in reader:
        if len(row) < 10:
            continue
        try:
            nloc = int(row[0])
            ccn = int(row[1])
            params = int(row[3])
            length = int(row[4])
            start_line = int(row[9])
        except ValueError:
            continue
        fpath = row[6]
        name = row[7]
        ext = os.path.splitext(fpath)[1].lower()
        results.append(FunctionResult(
            file=fpath,
            language=EXT_TO_LANG.get(ext, ext.lstrip(".").upper() or "unknown"),
            name=name,
            complexity=ccn,
            nloc=nloc,
            params=params,
            length=length,
            start_line=start_line,
        ))
    return results


def render_report(
    results: list[FunctionResult],
    threshold: int,
    root: Path,
    show_all: bool,
) -> str:
    filtered = results if show_all else [r for r in results if r.complexity >= threshold]
    filtered.sort(key=lambda r: (-r.complexity, r.file, r.start_line))

    by_lang: dict[str, list[FunctionResult]] = {}
    for r in results:
        by_lang.setdefault(r.language, []).append(r)

    files_scanned = len({r.file for r in results})

    lines: list[str] = []
    lines.append("# Cyclomatic Complexity Report")
    lines.append("")
    lines.append(f"- **Root:** `{root}`")
    lines.append(f"- **Engine:** lizard")
    lines.append(f"- **Files analyzed:** {files_scanned}")
    lines.append(f"- **Functions analyzed:** {len(results)}")
    lines.append(f"- **Threshold:** {threshold}")
    lines.append(f"- **Functions at or above threshold:** "
                 f"{sum(1 for r in results if r.complexity >= threshold)}")
    lines.append("")

    if by_lang:
        lines.append("## Languages analyzed")
        lines.append("")
        lines.append("| Language | Functions | Max CC | Avg CC |")
        lines.append("|---|---:|---:|---:|")
        for name in sorted(by_lang):
            bucket = by_lang[name]
            max_cc = max(r.complexity for r in bucket)
            avg_cc = sum(r.complexity for r in bucket) / len(bucket)
            lines.append(f"| {name} | {len(bucket)} | {max_cc} | {avg_cc:.1f} |")
        lines.append("")

    heading = "All functions" if show_all else f"Functions with CC ≥ {threshold}"
    lines.append(f"## {heading}")
    lines.append("")
    if not filtered:
        lines.append("_None._")
        lines.append("")
        return "\n".join(lines)

    lines.append("| # | Function | File:Line | Language | CC | NLOC | Params | Length |")
    lines.append("|---:|---|---|---|---:|---:|---:|---:|")
    for idx, r in enumerate(filtered, start=1):
        try:
            rel = os.path.relpath(r.file, root)
        except ValueError:
            rel = r.file
        lines.append(
            f"| {idx} | `{r.name}` | `{rel}:{r.start_line}` | {r.language} | "
            f"{r.complexity} | {r.nloc} | {r.params} | {r.length} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lowcc",
        description="Report functions with high cyclomatic complexity (powered by lizard).",
    )
    parser.add_argument("path", type=Path, help="Repository or folder to scan.")
    parser.add_argument(
        "-t", "--threshold", type=int, default=10,
        help="Minimum function CC for a function to appear in the report (default: 10).",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("lowcc-report.md"),
        help="Markdown output file (default: lowcc-report.md). Use - for stdout.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Include every analyzed function in the table, not just those over the threshold.",
    )
    parser.add_argument(
        "-x", "--exclude", action="append", default=[], metavar="GLOB",
        help="Additional glob to pass to lizard's -x. Repeatable. "
             "Combined with built-in defaults (node_modules, vendor, dist, build, .git, ...).",
    )
    parser.add_argument(
        "--no-default-excludes", action="store_true",
        help="Disable the built-in exclude list (only --exclude patterns will be used).",
    )
    args = parser.parse_args(argv)

    if not args.path.exists():
        sys.stderr.write(f"error: path does not exist: {args.path}\n")
        return 2
    if not args.path.is_dir() and not args.path.is_file():
        sys.stderr.write(f"error: path is not a file or directory: {args.path}\n")
        return 2

    lizard = require_lizard()
    root = args.path.resolve()
    excludes = list(args.exclude)
    if not args.no_default_excludes:
        excludes = list(DEFAULT_EXCLUDE_GLOBS) + excludes
    csv_text = run_lizard(lizard, root, excludes)
    results = parse_csv(csv_text)
    report = render_report(results, args.threshold, root, args.all)

    if str(args.output) == "-":
        sys.stdout.write(report)
    else:
        args.output.write_text(report, encoding="utf-8")
        flagged = sum(1 for r in results if r.complexity >= args.threshold)
        files_scanned = len({r.file for r in results})
        print(f"Analyzed {len(results)} functions across {files_scanned} files; "
              f"{flagged} at or above CC {args.threshold}.")
        print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
