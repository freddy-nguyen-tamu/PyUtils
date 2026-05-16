#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class TscDiag:
    file: str
    line: int
    col: int
    code: str
    message: str


# Example:
# src/renderer/components/ExportDialog.tsx(46,12): error TS2339: ...
# /abs/path/file.tsx(46,12): error TS2339: ...
DIAG_RE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s+error\s+TS(?P<code>\d+):\s+(?P<msg>.*)$"
)

# Matches:
#   \\wsl.localhost\Ubuntu\home\...
#   \\wsl$\Ubuntu\home\...
WSL_UNC_RE = re.compile(r"^\\\\(wsl\.localhost|wsl\$)\\([^\\]+)\\(.*)", re.IGNORECASE)


def unc_to_wsl(path: str) -> Tuple[str, str]:
    m = WSL_UNC_RE.match(path)
    if not m:
        raise ValueError("Not a WSL UNC path")
    distro = m.group(2)
    linux_path = "/" + m.group(3).replace("\\", "/")
    return distro, linux_path


def normalize_linux_path(p: str) -> str:
    return os.path.normpath(p.replace("\\", "/")).replace("\\", "/")


def find_tsconfig_near(win_path: Path) -> Optional[Path]:
    cur = win_path if win_path.is_dir() else win_path.parent
    for parent in [cur, *cur.parents]:
        candidate = parent / "tsconfig.json"
        if candidate.exists():
            return candidate
    return None


def wsl_run_stream(distro: str, bash_cmd: str) -> tuple[int, str]:
    """Run a bash command in WSL, stream output live, also return full output."""
    cmd = ["wsl.exe", "-d", distro, "--", "bash", "-lc", bash_cmd]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    out_lines: List[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
        out_lines.append(line)

    proc.wait()
    return proc.returncode, "".join(out_lines)


def ensure_typescript_local(distro: str, project_dir: str) -> bool:
    """Ensure ./node_modules/.bin/tsc exists by installing typescript locally if missing."""
    print("▶ Checking for local TypeScript (./node_modules/.bin/tsc)...")
    check_cmd = f'cd "{project_dir}" && test -x "./node_modules/.bin/tsc"'
    code, _ = wsl_run_stream(distro, check_cmd)
    if code == 0:
        print(" Local tsc found.\n")
        return True

    print("ℹ Local tsc not found. Installing TypeScript locally (devDependency)...")
    install_cmd = f'cd "{project_dir}" && npm install --no-fund --no-audit -D typescript'
    code, _ = wsl_run_stream(distro, install_cmd)
    if code != 0:
        print(" Failed to install TypeScript. Make sure npm works in WSL and rerun.", file=sys.stderr)
        return False

    print(" TypeScript installed.\n")
    return True


def run_tsc_local(distro: str, project_dir: str, tsconfig_linux: str) -> str:
    print("▶ Running TypeScript compiler (local ./node_modules/.bin/tsc)...")
    print(f"  Project dir: {project_dir}")
    print(f"  tsconfig:    {tsconfig_linux}\n")

    # Add skipLibCheck to speed up and reduce noise if you want:
    #   --skipLibCheck
    tsc_cmd = (
        f'cd "{project_dir}" && '
        f'./node_modules/.bin/tsc -p "{tsconfig_linux}" --noEmit --pretty false'
    )
    code, out = wsl_run_stream(distro, tsc_cmd)
    print("\n▶ tsc finished with exit code", code)
    return out


def parse_diagnostics(output: str) -> List[TscDiag]:
    diags: List[TscDiag] = []
    pending: Optional[TscDiag] = None

    for line in output.splitlines():
        m = DIAG_RE.match(line)
        if m:
            if pending:
                diags.append(pending)
            pending = TscDiag(
                file=m.group("file"),
                line=int(m.group("line")),
                col=int(m.group("col")),
                code=m.group("code"),
                message=m.group("msg").strip(),
            )
        elif pending and line.strip():
            pending = TscDiag(
                pending.file,
                pending.line,
                pending.col,
                pending.code,
                (pending.message + " " + line.strip()).strip(),
            )

    if pending:
        diags.append(pending)

    return diags


def resolve_diag_path(diag_file: str, project_dir: str) -> str:
    """
    tsc may output either:
      - absolute: /home/.../file.tsx
      - relative: src/.../file.tsx
    Normalize to an absolute-ish path under project_dir.
    """
    p = normalize_linux_path(diag_file)
    if p.startswith("/"):
        return p
    return normalize_linux_path(f"{project_dir}/{p}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="WSL UNC path to .ts/.tsx file")
    ap.add_argument("-p", "--project", help="Optional WSL UNC path to tsconfig.json")
    args = ap.parse_args()

    print("▶ Parsing WSL path...")
    try:
        distro, file_linux = unc_to_wsl(args.file)
    except ValueError:
        print(
            r"Expected WSL UNC path like \\wsl.localhost\Ubuntu\home\...\file.tsx or \\wsl$\Ubuntu\...",
            file=sys.stderr,
        )
        return 2

    file_linux_norm = normalize_linux_path(file_linux)
    file_win = Path(args.file)

    if not file_win.exists():
        print(" File not found via Windows UNC path.", file=sys.stderr)
        return 2

    print(" File resolved")
    print(f"  Linux path: {file_linux_norm}")

    if args.project:
        _, tsconfig_linux = unc_to_wsl(args.project)
        tsconfig_win = Path(args.project)
        if not tsconfig_win.exists():
            print(" tsconfig.json not found.", file=sys.stderr)
            return 2
    else:
        print("▶ Searching for tsconfig.json...")
        tsconfig_win = find_tsconfig_near(file_win)
        if not tsconfig_win:
            print(" Could not auto-find tsconfig.json.", file=sys.stderr)
            return 2
        _, tsconfig_linux = unc_to_wsl(str(tsconfig_win))
        print(f" tsconfig found: {tsconfig_linux}")

    tsconfig_linux = normalize_linux_path(tsconfig_linux)
    project_dir = normalize_linux_path(os.path.dirname(tsconfig_linux) or ".")

    if not ensure_typescript_local(distro, project_dir):
        return 2

    tsc_output = run_tsc_local(distro, project_dir, tsconfig_linux)

    print("\n▶ Parsing TypeScript diagnostics...")
    diags = parse_diagnostics(tsc_output)

    # --- IMPORTANT FIX: match relative or absolute diagnostic file paths ---
    target_abs = file_linux_norm
    target_rel = normalize_linux_path(os.path.relpath(target_abs, start=project_dir))

    matched: List[TscDiag] = []
    for d in diags:
        diag_abs = resolve_diag_path(d.file, project_dir)

        if diag_abs == target_abs:
            matched.append(d)
            continue

        diag_rel = normalize_linux_path(os.path.relpath(diag_abs, start=project_dir))
        if diag_rel == target_rel:
            matched.append(d)

    if not matched:
        print(" No TypeScript errors found for this file.")
        return 0

    lines = file_win.read_text(encoding="utf-8", errors="replace").splitlines()

    by_line = defaultdict(list)
    for d in matched:
        by_line[d.line].append(d)

    print(f" Found {len(matched)} error(s) in this file:")
    for ln in sorted(by_line):
        src = lines[ln - 1] if 1 <= ln <= len(lines) else ""
        print(f"\nLine {ln}: {src}")
        for d in sorted(by_line[ln], key=lambda x: x.col):
            print(f"  (col {d.col}) TS{d.code}: {d.message}")

    return 1


if __name__ == "__main__":
    sys.exit(main())