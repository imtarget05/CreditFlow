#!/usr/bin/env python3
"""Sinh danh sách dependency cần để import một entrypoint, từ đồ thị import thật.

Dùng:  python3 tools/import_closure.py --root <repo> --entry backend.app

Câu chuyện của tool này, tóm lại những gì đã mất thời gian ở MAIA
----------------------------------------------------------------
1. Quét MỘT file là không đủ. `api.py` chỉ ghi `from maia.auth import ...`;
   passlib nằm trong auth.py. Image build xanh rồi mới crash lúc uvicorn import.
2. Phải resolve RELATIVE import. Không thì chính module của dự án bị tính nhầm
   là package của bên thứ ba.
3. Chỉ đọc `tree.body` thì bỏ sót import trong `try:`/`if:` Ở CẤP MODULE — chúng
   vẫn chạy lúc import. Đây là lý do `langgraph` biến mất lúc đầu.
4. Import trong thân hàm KHÔNG chặn khởi động. `ast.walk` lặn vào thân hàm sẽ
   kéo cả `torch` vào, biến image thành hàng GB.
5. Phải nạp `__init__.py` của MỌI package cha. Import `maia.agent.mcp_dispatch`
   chạy `maia/agent/__init__.py` trước, và đó mới là nơi cần dependency.
6. `Lambda.body` là `ast.Call` chứ không phải list — dùng `iter_child_nodes`.

Điểm mấu chốt là (4) đối lập với (3) và (5): cần đi sâu hơn `tree.body`, nhưng
dừng lại ở ranh giới thân hàm. Vì vậy tool báo HAI nhóm, và chỉ nhóm REQUIRED
mới quyết định image.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

DEFER_BOUNDARIES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
STDLIB = set(sys.stdlib_module_names)


class Resolver:
    def __init__(self, root: Path, local_prefixes: list[str]):
        self.root = root
        self.local_prefixes = tuple(local_prefixes)

    def path_for(self, module: str) -> Path | None:
        rel = module.replace(".", "/")
        for candidate in (self.root / f"{rel}.py", self.root / rel / "__init__.py"):
            if candidate.exists():
                return candidate
        return None

    def is_local(self, module: str) -> bool:
        head = module.split(".", 1)[0]
        return head in self.local_prefixes

    def ancestors(self, module: str):
        parts = module.split(".")
        for depth in range(len(parts) - 1, 0, -1):
            yield ".".join(parts[:depth])

    def imports_of(self, path: Path, include_deferred: bool, package: str):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return

        def absolute(node) -> str:
            if not node.level:
                return node.module
            base = package.split(".") if package else []
            trimmed = base[: len(base) - (node.level - 1)] if node.level > 1 else base
            return ".".join([*trimmed, node.module])

        stack = list(tree.body)
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                yield absolute(node)
            elif isinstance(node, DEFER_BOUNDARIES):
                if include_deferred:
                    stack.extend(ast.iter_child_nodes(node))
            else:
                # if / try / with / for ở cấp module: vẫn là import-time.
                stack.extend(ast.iter_child_nodes(node))

    def collect(self, entry: str, include_deferred: bool):
        seen: set[str] = set()
        external: set[str] = set()
        unresolved: set[str] = set()
        queue = [entry]
        while queue:
            module = queue.pop()
            if module in seen:
                continue
            seen.add(module)
            path = self.path_for(module)
            if path is None:
                if self.is_local(module):
                    unresolved.add(module)
                continue
            for parent in self.ancestors(module):
                queue.append(parent)
            is_package = path.name == "__init__.py"
            package = module if is_package else module.rsplit(".", 1)[0]
            if is_package and "." not in module:
                package = ""
            for imported in self.imports_of(path, include_deferred, package):
                if self.is_local(imported) and self.path_for(imported):
                    queue.append(imported)
                    continue
                head = imported.split(".", 1)[0]
                if self.is_local(head):
                    unresolved.add(imported)
                elif head not in STDLIB and head != "__future__":
                    external.add(head)
        return seen, external, unresolved


HEAVY = {"torch", "onnxruntime", "sentence_transformers", "transformers", "faiss"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="repo root")
    parser.add_argument("--entry", required=True, help="entry module, e.g. backend.app")
    parser.add_argument("--local", nargs="+", required=True,
                        help="top-level package names owned by this repo")
    args = parser.parse_args()

    resolver = Resolver(Path(args.root).resolve(), args.local)
    modules, required, unresolved = resolver.collect(args.entry, False)
    _, everything, _ = resolver.collect(args.entry, True)
    lazy_only = everything - required

    print(f"entry     : {args.entry}")
    print(f"modules   : {len(modules)} walked")
    if unresolved:
        print(f"UNRESOLVED: {sorted(unresolved)}")
    print()
    print(f"REQUIRED AT IMPORT TIME ({len(required)}):")
    for name in sorted(required):
        print(f"  {name}")
    print()
    print(f"LAZY-ONLY ({len(lazy_only)}):")
    for name in sorted(lazy_only):
        print(f"  {name}{'  <-- HEAVY' if name in HEAVY else ''}")
    print()
    print(f"heavy libs required at import time: {sorted(required & HEAVY) or 'NONE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
