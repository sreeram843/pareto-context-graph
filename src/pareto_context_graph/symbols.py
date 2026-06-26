"""Symbol extraction for the search index (regex-based, zero runtime deps)."""

from __future__ import annotations

import re
from pathlib import Path

_SYMBOL_DEFS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^[ \t]*(?:async\s+)?def\s+(\w+)", re.MULTILINE), "function"),
    (re.compile(r"^[ \t]*class\s+(\w+)", re.MULTILINE), "class"),
    (re.compile(r"^[ \t]*def\s+(\w+)", re.MULTILINE), "function"),
    (re.compile(r"^[ \t]*(?:class|module)\s+(\w+)", re.MULTILINE), "class"),
    (re.compile(r"^[ \t]*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE), "function"),
    (re.compile(r"^[ \t]*(?:export\s+)?class\s+(\w+)", re.MULTILINE), "class"),
    (
        re.compile(
            r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", re.MULTILINE
        ),
        "function",
    ),
    (re.compile(r"^[ \t]*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.MULTILINE), "function"),
    (re.compile(r"^[ \t]*(?:pub\s+)?(?:struct|trait|enum|impl)\s+(\w+)", re.MULTILINE), "class"),
    (re.compile(r"^func\s+(?:\(\w+\s+[^)]+\)\s+)?(\w+)", re.MULTILINE), "function"),
    (re.compile(r"^type\s+(\w+)\s+(?:struct|interface)", re.MULTILINE), "class"),
]

_SKIP_NAMES = frozenset(
    {
        "self",
        "cls",
        "this",
        "new",
        "return",
        "if",
        "else",
        "for",
        "while",
        "try",
        "catch",
        "throw",
        "import",
        "from",
        "in",
        "not",
        "and",
        "or",
        "is",
        "None",
        "True",
        "False",
        "null",
        "undefined",
        "var",
        "let",
        "const",
    }
)

CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyx",
        ".rb",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".cs",
        ".swift",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".sql",
    }
)


def extract_symbol_records(
    file_path: Path, *, max_bytes: int = 50_000, use_treesitter: bool = False
) -> list[dict]:
    """Return symbol records: symbol, kind, line, container."""
    if use_treesitter:
        ts_records = _extract_treesitter_records(file_path, max_bytes=max_bytes)
        if ts_records:
            return ts_records
    return _extract_regex_records(file_path, max_bytes=max_bytes)


def _extract_regex_records(file_path: Path, *, max_bytes: int = 50_000) -> list[dict]:
    try:
        content = file_path.read_text(errors="ignore")[:max_bytes]
    except (OSError, UnicodeDecodeError):
        return []

    if not content:
        return []

    lines = content.splitlines()
    seen: set[tuple[str, int]] = set()
    records: list[dict] = []

    for pattern, kind in _SYMBOL_DEFS:
        for match in pattern.finditer(content):
            name = match.group(1)
            if name in _SKIP_NAMES:
                continue
            line_no = content[: match.start()].count("\n") + 1
            key = (name, line_no)
            if key in seen:
                continue
            seen.add(key)
            container = _container_for_line(lines, line_no - 1)
            records.append(
                {
                    "symbol": name,
                    "kind": kind,
                    "line": line_no,
                    "container": container,
                }
            )

    return records


def _container_for_line(lines: list[str], line_idx: int) -> str:
    """Best-effort enclosing class/module name for a definition line."""
    for idx in range(line_idx - 1, -1, -1):
        line = lines[idx]
        class_match = re.match(r"^[ \t]*class\s+(\w+)", line)
        if class_match:
            return class_match.group(1)
        module_match = re.match(r"^[ \t]*(?:class|module)\s+(\w+)", line)
        if module_match:
            return module_match.group(1)
    return ""


def _treesitter_available() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_python  # noqa: F401
    except ImportError:
        return False
    return True


def _parser_for_suffix(suffix: str):
    from tree_sitter import Language, Parser

    if suffix == ".py":
        import tree_sitter_python as lang_mod

        language = Language(lang_mod.language())
    elif suffix == ".go":
        try:
            import tree_sitter_go as lang_mod
        except ImportError:
            return None
        language = Language(lang_mod.language())
    else:
        return None
    parser = Parser(language)
    return parser


def _extract_treesitter_records(file_path: Path, *, max_bytes: int = 50_000) -> list[dict]:
    if not _treesitter_available():
        return []
    suffix = file_path.suffix.lower()
    parser = _parser_for_suffix(suffix)
    if parser is None:
        return []
    try:
        content = file_path.read_bytes()[:max_bytes]
    except OSError:
        return []
    if not content:
        return []

    tree = parser.parse(content)
    records: list[dict] = []

    def walk(node, container: str = "") -> None:
        ntype = node.type
        if ntype in ("function_definition", "method_definition"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = content[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="ignore"
                )
                if name and name not in _SKIP_NAMES:
                    records.append(
                        {
                            "symbol": name,
                            "kind": "function",
                            "line": node.start_point[0] + 1,
                            "container": container,
                        }
                    )
        elif ntype in ("class_definition",):
            name_node = node.child_by_field_name("name")
            class_name = container
            if name_node is not None:
                class_name = content[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="ignore"
                )
                if class_name and class_name not in _SKIP_NAMES:
                    records.append(
                        {
                            "symbol": class_name,
                            "kind": "class",
                            "line": node.start_point[0] + 1,
                            "container": container,
                        }
                    )
        elif ntype in ("function_declaration", "method_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = content[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="ignore"
                )
                if name and name not in _SKIP_NAMES:
                    records.append(
                        {
                            "symbol": name,
                            "kind": "function",
                            "line": node.start_point[0] + 1,
                            "container": container,
                        }
                    )
        elif ntype in ("type_declaration",):
            for child in node.children:
                if child.type == "type_spec":
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        name = content[name_node.start_byte : name_node.end_byte].decode(
                            "utf-8", errors="ignore"
                        )
                        if name and name not in _SKIP_NAMES:
                            records.append(
                                {
                                    "symbol": name,
                                    "kind": "class",
                                    "line": child.start_point[0] + 1,
                                    "container": container,
                                }
                            )
        child_container = container
        if ntype in ("class_definition",):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                child_container = content[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="ignore"
                )
        for child in node.children:
            walk(child, child_container)

    walk(tree.root_node)
    return records
