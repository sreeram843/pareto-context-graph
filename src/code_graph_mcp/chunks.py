"""Chunk-level code extraction, symbol indexing, and keyword analysis.

Provides function/class boundary detection, symbol extraction for precise
context, and TF-IDF keyword scoring — all with zero external dependencies.
"""

from __future__ import annotations

import re
import math
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Chunk extraction: find function/class boundaries via regex
# ---------------------------------------------------------------------------

# Patterns to detect function/class/method boundaries
_CHUNK_PATTERNS = [
    # Python: def/class/async def
    re.compile(r'^([ \t]*)((?:async\s+)?def\s+\w+|class\s+\w+)', re.MULTILINE),
    # Ruby: def/class/module
    re.compile(r'^([ \t]*)(def\s+\w+|class\s+\w+|module\s+\w+)', re.MULTILINE),
    # RSpec/Ruby test blocks: describe, context, it, shared_examples
    re.compile(r'^([ \t]*)((?:RSpec\.)?(?:shared_examples_for|shared_context|describe|context|it)\s+[^\n]*\bdo)\s*$', re.MULTILINE),
    # SQL CTEs: WITH name AS ( or , name AS (
    re.compile(r'^([ \t]*)((?:with|,)\s+\w+\s+as\s*\()', re.MULTILINE | re.IGNORECASE),
    # JS/TS: function declarations, arrow functions, class, methods
    re.compile(r'^([ \t]*)((?:export\s+)?(?:async\s+)?function\s+\w+|class\s+\w+|(?:export\s+)?(?:const|let)\s+\w+\s*=\s*(?:async\s*)?\()', re.MULTILINE),
    # C/C++/Java/Go: return_type function_name(
    re.compile(r'^([ \t]*)(\w[\w:*&<> ]*\s+\w+\s*\([^)]*\)\s*(?:const\s*)?)\{?\s*$', re.MULTILINE),
    # Rust: fn/impl/struct/trait
    re.compile(r'^([ \t]*)((?:pub\s+)?(?:async\s+)?fn\s+\w+|impl\s+\w+|struct\s+\w+|trait\s+\w+)', re.MULTILINE),
]

# End-of-block detection for indentation-based languages
_INDENT_LANGUAGES = {'.py', '.pyx', '.rb'}
# Brace-based languages
_BRACE_LANGUAGES = {'.js', '.ts', '.tsx', '.jsx', '.java', '.c', '.cpp',
                    '.h', '.hpp', '.rs', '.go', '.cs', '.swift', '.kt'}


def extract_chunks(file_path: Path) -> list[dict]:
    """Extract function/class chunks from a file.

    Returns list of {name, start_line, end_line, signature, body}.
    Lines are 1-indexed.
    """
    try:
        content = file_path.read_text(errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []

    lines = content.splitlines()
    if not lines:
        return []

    ext = file_path.suffix.lower()
    chunks: list[dict] = []

    # Find all chunk start positions
    starts: list[tuple[int, int, str]] = []  # (line_no, indent_level, signature)
    for pattern in _CHUNK_PATTERNS:
        for match in pattern.finditer(content):
            line_no = content[:match.start()].count('\n')  # 0-indexed
            indent = len(match.group(1).replace('\t', '    '))
            signature = match.group(2).strip()
            starts.append((line_no, indent, signature))

    if not starts:
        return []

    # Deduplicate by line number (multiple patterns can match same line)
    seen_lines: set[int] = set()
    deduped: list[tuple[int, int, str]] = []
    for item in starts:
        if item[0] not in seen_lines:
            seen_lines.add(item[0])
            deduped.append(item)
    starts = deduped

    # Sort by line number
    starts.sort(key=lambda x: x[0])

    # Determine chunk boundaries
    for i, (start_line, indent, signature) in enumerate(starts):
        # End = next chunk at same or lower indent, or EOF
        end_line = len(lines) - 1
        if ext in _INDENT_LANGUAGES:
            # Python: find next line at same or lower indentation
            for j in range(start_line + 1, len(lines)):
                line = lines[j]
                if not line.strip():
                    continue
                line_indent = len(line) - len(line.lstrip())
                if line_indent <= indent and j > start_line + 1:
                    end_line = j - 1
                    break
        else:
            # Brace languages: count braces
            brace_count = 0
            found_open = False
            for j in range(start_line, len(lines)):
                brace_count += lines[j].count('{') - lines[j].count('}')
                if '{' in lines[j]:
                    found_open = True
                if found_open and brace_count <= 0:
                    end_line = j
                    break
            if not found_open:
                # No braces — use indent-based detection (handles do...end, Ruby, etc.)
                for j in range(start_line + 1, len(lines)):
                    line = lines[j]
                    if not line.strip():
                        continue
                    line_indent = len(line) - len(line.lstrip())
                    if line_indent <= indent:
                        end_line = j  # include the closing 'end' line
                        break
                else:
                    end_line = min(start_line + 100, len(lines) - 1)

        # Extract name from signature
        name_match = re.search(r'(?:def|function|class|module|fn|impl|struct|trait|const|let)\s+(\w+)', signature)
        if name_match:
            name = name_match.group(1)
        else:
            # RSpec/test blocks: extract description string
            desc_match = re.search(r'(?:describe|context|it|shared_examples_for|shared_context)\s+["\']([^"\']{1,60})["\']', signature)
            if desc_match:
                name = desc_match.group(1)
            else:
                # SQL CTEs: extract CTE name
                cte_match = re.search(r'(?:with|,)\s+(\w+)\s+as\s*\(', signature, re.IGNORECASE)
                name = cte_match.group(1) if cte_match else signature[:40]

        body = '\n'.join(lines[start_line:end_line + 1])
        # Cap chunk size
        if len(body) > 5000:
            body = body[:5000] + '\n# ... truncated'

        chunks.append({
            'name': name,
            'start_line': start_line + 1,  # 1-indexed
            'end_line': end_line + 1,
            'signature': signature,
            'body': body,
        })

    return chunks


def get_relevant_chunks(
    file_path: Path,
    query: str = "",
    seed_imports: list[str] | None = None,
    max_chunks: int = 5,
) -> list[dict]:
    """Get the most relevant chunks from a file based on query and context.

    Scoring:
    - Query term match in chunk name/body: +10
    - Referenced by seed imports: +5
    - Chunk is a class: +3
    - Chunk is short (< 30 lines): +1
    """
    chunks = extract_chunks(file_path)
    if not chunks:
        # If no chunks found, return a simple top-of-file summary
        return []

    query_terms = set(query.lower().split()) if query else set()
    import_names = set(seed_imports or [])

    for chunk in chunks:
        score = 0
        chunk_text = (chunk['name'] + ' ' + chunk['body']).lower()

        # Query relevance
        for term in query_terms:
            if term in chunk_text:
                score += 10

        # Import reference (seed file imports this symbol)
        if chunk['name'] in import_names:
            score += 15

        # Class definitions are high-value
        if 'class' in chunk['signature'].lower():
            score += 3

        chunk['_score'] = score

    # Sort by score, keep top N
    chunks.sort(key=lambda c: -c['_score'])
    # Always include scored > 0, then fill with top chunks
    relevant = [c for c in chunks if c['_score'] > 0]
    if not relevant:
        relevant = chunks[:max_chunks]
    else:
        relevant = relevant[:max_chunks]

    # Clean internal keys
    for c in relevant:
        c.pop('_score', None)

    return relevant


# ---------------------------------------------------------------------------
# Symbol extraction: lightweight function/class/method index
# ---------------------------------------------------------------------------

_SYMBOL_PATTERNS = [
    # Python
    re.compile(r'^[ \t]*(?:async\s+)?def\s+(\w+)', re.MULTILINE),
    re.compile(r'^[ \t]*class\s+(\w+)', re.MULTILINE),
    # Ruby
    re.compile(r'^[ \t]*def\s+(\w+)', re.MULTILINE),
    re.compile(r'^[ \t]*(?:class|module)\s+(\w+)', re.MULTILINE),
    # RSpec: describe/context block subjects
    re.compile(r'^[ \t]*(?:RSpec\.)?(?:describe|context)\s+["\']([^"\']+)["\']', re.MULTILINE),
    # JS/TS
    re.compile(r'^[ \t]*(?:export\s+)?(?:async\s+)?function\s+(\w+)', re.MULTILINE),
    re.compile(r'^[ \t]*(?:export\s+)?class\s+(\w+)', re.MULTILINE),
    re.compile(r'^[ \t]*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(', re.MULTILINE),
    # Rust
    re.compile(r'^[ \t]*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', re.MULTILINE),
    re.compile(r'^[ \t]*(?:pub\s+)?(?:struct|trait|enum|impl)\s+(\w+)', re.MULTILINE),
    # Go
    re.compile(r'^func\s+(?:\(\w+\s+[^)]+\)\s+)?(\w+)', re.MULTILINE),
    re.compile(r'^type\s+(\w+)\s+(?:struct|interface)', re.MULTILINE),
    # C/Java
    re.compile(r'^[ \t]*(?:public|private|protected|static|virtual|override)?\s*\w+[\w<>*&: ]*\s+(\w+)\s*\(', re.MULTILINE),
]


def extract_symbols(file_path: Path) -> list[str]:
    """Extract all function/class/method names from a file."""
    try:
        content = file_path.read_text(errors="ignore")[:50000]  # First 50KB
    except (OSError, UnicodeDecodeError):
        return []

    symbols: set[str] = set()
    for pattern in _SYMBOL_PATTERNS:
        for match in pattern.finditer(content):
            name = match.group(1)
            # Skip common noise words
            if name not in ('self', 'cls', 'this', 'new', 'return', 'if', 'else',
                           'for', 'while', 'try', 'catch', 'throw', 'import',
                           'from', 'in', 'not', 'and', 'or', 'is', 'None', 'True',
                           'False', 'null', 'undefined', 'var', 'let', 'const'):
                symbols.add(name)

    return sorted(symbols)


def get_signatures(file_path: Path) -> list[str]:
    """Get function/class signatures (declarations without bodies).

    This is "Tier 2" — more than a summary, less than full content.
    """
    try:
        content = file_path.read_text(errors="ignore")[:50000]
    except (OSError, UnicodeDecodeError):
        return []

    signatures: list[str] = []
    for pattern in _CHUNK_PATTERNS:
        for match in pattern.finditer(content):
            sig = match.group(2).strip()
            # For Python, capture the full signature including params
            line_start = match.start()
            line_end = content.find('\n', line_start)
            if line_end == -1:
                line_end = len(content)
            full_line = content[line_start:line_end].strip()
            # Trim to just the declaration
            if len(full_line) > 200:
                full_line = full_line[:200] + '...'
            signatures.append(full_line)

    return signatures


# ---------------------------------------------------------------------------
# Keyword index: TF-IDF over identifiers (no external deps)
# ---------------------------------------------------------------------------

# Split identifiers on camelCase and snake_case boundaries
_SPLIT_PATTERN = re.compile(r'[_\-./\\]|(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
_WORD_PATTERN = re.compile(r'[a-zA-Z]\w{2,}')  # At least 3 chars


def _tokenize_identifiers(content: str) -> list[str]:
    """Extract and split identifiers from source code into searchable terms."""
    words = _WORD_PATTERN.findall(content)
    tokens: list[str] = []
    for word in words:
        # Split camelCase/snake_case
        parts = _SPLIT_PATTERN.split(word)
        for part in parts:
            lower = part.lower()
            if len(lower) >= 3 and lower not in _STOP_WORDS:
                tokens.append(lower)
        # Also keep the full word
        lower_word = word.lower()
        if len(lower_word) >= 3:
            tokens.append(lower_word)
    return tokens


_STOP_WORDS = frozenset([
    'the', 'and', 'for', 'not', 'but', 'are', 'was', 'were', 'been', 'have',
    'has', 'had', 'does', 'did', 'will', 'would', 'could', 'should', 'may',
    'might', 'shall', 'can', 'this', 'that', 'these', 'those', 'with',
    'from', 'into', 'each', 'all', 'any', 'both', 'self', 'return',
    'true', 'false', 'none', 'null', 'undefined', 'import', 'export',
    'class', 'def', 'function', 'var', 'let', 'const', 'new', 'end',
    'begin', 'else', 'elif', 'elsif', 'then', 'while', 'until',
    'string', 'int', 'integer', 'float', 'bool', 'boolean', 'void',
    'private', 'public', 'protected', 'static', 'require', 'include',
])


class KeywordIndex:
    """Lightweight TF-IDF index over file identifiers.

    Build once during graph construction, query during get_context.
    """

    def __init__(self) -> None:
        # file_path → Counter of term frequencies
        self._tf: dict[str, Counter] = {}
        # term → number of documents containing it
        self._df: Counter = Counter()
        self._total_docs: int = 0

    def index_file(self, file_path: str, content: str) -> None:
        """Add a file to the keyword index."""
        tokens = _tokenize_identifiers(content[:20000])  # First 20KB
        if not tokens:
            return
        tf = Counter(tokens)
        self._tf[file_path] = tf
        self._total_docs += 1
        # Update document frequency
        for term in tf:
            self._df[term] += 1

    def query(self, text: str, top_n: int = 20) -> list[tuple[str, float]]:
        """Find files most relevant to a query text, ranked by TF-IDF score.

        Returns list of (file_path, score) tuples.
        """
        query_terms = set(t.lower() for t in _WORD_PATTERN.findall(text))
        # Also split on camelCase/snake_case
        expanded: set[str] = set()
        for term in query_terms:
            parts = _SPLIT_PATTERN.split(term)
            for part in parts:
                lower = part.lower()
                if len(lower) >= 3 and lower not in _STOP_WORDS:
                    expanded.add(lower)
            if len(term) >= 3:
                expanded.add(term)

        if not expanded or self._total_docs == 0:
            return []

        scores: dict[str, float] = {}
        for file_path, tf in self._tf.items():
            score = 0.0
            for term in expanded:
                if term in tf:
                    # TF-IDF: tf * log(N / df)
                    df = self._df.get(term, 1)
                    idf = math.log(self._total_docs / df) if df > 0 else 0
                    score += tf[term] * idf
            if score > 0:
                scores[file_path] = score

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return ranked[:top_n]

    def get_file_keywords(self, file_path: str, top_n: int = 10) -> list[str]:
        """Get top keywords for a file (by TF-IDF within that file)."""
        tf = self._tf.get(file_path)
        if not tf:
            return []
        scored = []
        for term, count in tf.items():
            df = self._df.get(term, 1)
            idf = math.log(self._total_docs / df) if df > 0 and self._total_docs > 0 else 0
            scored.append((term, count * idf))
        scored.sort(key=lambda x: -x[1])
        return [term for term, _ in scored[:top_n]]
