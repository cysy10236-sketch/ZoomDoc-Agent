from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pymupdf as fitz

from zoomdoc.schemas import DocTreeNode, DocumentTree
from zoomdoc.vision.render import sha256_file

if TYPE_CHECKING:
    pass

_INJECTION_PATTERNS = re.compile(
    r"(?i)("
    r"system\s+override|"
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?|"
    r"forget\s+(all\s+)?(previous|prior|above)\s+instructions?|"
    r"you\s+are\s+now|act\s+as|new\s+instructions?|"
    r"do\s+not\s+follow|override\s+(the\s+)?(system|previous|prior)|"
    r"disregard|jailbreak|ALL\s+sections\s+MUST"
    r")"
)

_LEAK_FILTER_PATTERN = re.compile(
    r"(?i)(data:image|base64,|[\w\-\./\\]+\.(?:png|jpe?g|webp|bmp))"
)


def sanitize_summary_text(text: str, max_chars: int = 200) -> str:
    """Clean and sanitize page text for safe embedding into tree node summaries.

    1. Collapse all newlines, tabs, and spaces.
    2. Redact known prompt-injection patterns.
    3. Filter out any file path / data-url patterns to prevent false-positive leakage alarms.
    4. Strip and truncate to max_chars.
    """
    cleaned = re.sub(r"\s+", " ", text)
    cleaned = _INJECTION_PATTERNS.sub("[REDACTED]", cleaned)
    cleaned = _LEAK_FILTER_PATTERN.sub("[CONTENT]", cleaned)
    cleaned = cleaned.strip()
    return cleaned[:max_chars]


def flatten_tree_nodes(tree: DocumentTree) -> dict[str, DocTreeNode]:
    """Map all node_ids to DocTreeNode instances across the whole tree."""
    mapping: dict[str, DocTreeNode] = {}

    def _traverse(node: DocTreeNode) -> None:
        mapping[node.node_id] = node
        for child in node.children:
            _traverse(child)

    for root in tree.roots:
        _traverse(root)
    return mapping


def get_leaf_nodes(tree: DocumentTree) -> list[DocTreeNode]:
    """Return all leaf nodes (children is empty) in pre-order."""
    leaves: list[DocTreeNode] = []

    def _collect(node: DocTreeNode) -> None:
        if not node.children:
            leaves.append(node)
        else:
            for child in node.children:
                _collect(child)

    for root in tree.roots:
        _collect(root)
    return leaves


def collect_candidate_pages(
    tree: DocumentTree,
    selected_node_ids: list[str],
    visited_node_ids: list[str],
    max_pages: int = 20,
) -> tuple[list[int], list[str]]:
    """Gather physical page numbers for selected nodes and cap strictly at max_pages.

    Returns:
        (candidate_pages, newly_visited_leaf_ids)
    """
    all_nodes = flatten_tree_nodes(tree)
    visited_set = set(visited_node_ids)

    candidate_leaves: list[DocTreeNode] = []
    seen_leaf_ids: set[str] = set()

    for nid in selected_node_ids:
        if nid not in all_nodes:
            continue
        node = all_nodes[nid]
        leaves: list[DocTreeNode] = []

        def _get_leaves(n: DocTreeNode) -> None:
            if not n.children:
                leaves.append(n)
            else:
                for c in n.children:
                    _get_leaves(c)

        _get_leaves(node)
        for leaf in leaves:
            if leaf.node_id not in visited_set and leaf.node_id not in seen_leaf_ids:
                seen_leaf_ids.add(leaf.node_id)
                candidate_leaves.append(leaf)

    accumulated_pages: set[int] = set()
    newly_visited_ids: list[str] = []

    for leaf in candidate_leaves:
        leaf_pages = set(range(leaf.start_page, leaf.end_page + 1))
        union_pages = accumulated_pages | leaf_pages

        if not accumulated_pages:
            accumulated_pages = union_pages
            newly_visited_ids.append(leaf.node_id)
        elif len(union_pages) <= max_pages:
            accumulated_pages = union_pages
            newly_visited_ids.append(leaf.node_id)
        else:
            break

    return (sorted(list(accumulated_pages)), newly_visited_ids)


def format_tree_outline(nodes: list[DocTreeNode], indent: int = 0) -> str:
    """Format a list of DocTreeNode objects into a structured textual outline."""
    lines: list[str] = []
    pad = "  " * indent
    for n in nodes:
        has_ch = bool(n.children)
        summary_part = f" | Preview: {n.summary}" if n.summary else ""
        lines.append(
            f"{pad}- [ID: {n.node_id}] {n.structure} {n.title} (Pages {n.start_page}-{n.end_page}, has_children={has_ch}){summary_part}"
        )
    return "\n".join(lines)


def _split_large_leaf(
    node: DocTreeNode,
    max_leaf_pages: int,
    counter: list[int],
) -> list[DocTreeNode]:
    """Recursively split a leaf node whose page_span > max_leaf_pages into multiple parts."""
    total_pages = node.page_span
    if total_pages <= max_leaf_pages:
        return [node]

    parts: list[DocTreeNode] = []
    curr_start = node.start_page
    part_idx = 1

    while curr_start <= node.end_page:
        curr_end = min(curr_start + max_leaf_pages - 1, node.end_page)
        nid = f"{counter[0]:04d}"
        counter[0] += 1
        part_node = DocTreeNode(
            node_id=nid,
            structure=f"{node.structure}.{part_idx}",
            title=f"{node.title} (Part {part_idx})",
            level=node.level + 1,
            start_page=curr_start,
            end_page=curr_end,
            summary=node.summary,
            children=[],
        )
        parts.append(part_node)
        curr_start = curr_end + 1
        part_idx += 1

    return parts


def build_document_tree(
    pdf_path: str,
    max_leaf_pages: int = 20,
    out_dir: str | None = None,
) -> DocumentTree:
    """Build a hierarchical DocumentTree from PDF bookmarks, heuristics, or uniform chunking.

    Guarantees:
    - Every leaf node page_span <= max_leaf_pages.
    - Page spans strictly cover 1..total_pages.
    - Results are idempotent if out_dir is provided.
    """
    doc_id = sha256_file(pdf_path)[:16]
    if out_dir is not None:
        cache_file = Path(out_dir) / "assets" / doc_id / "tree.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return DocumentTree.model_validate(data)
            except Exception:
                pass

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        return DocumentTree(doc_id=doc_id, total_pages=0, max_leaf_pages=max_leaf_pages, roots=[])

    # 1. Try reading native PDF TOC Bookmarks
    raw_toc = doc.get_toc()  # [[level, title, page], ...]
    cleaned_toc: list[list[Any]] = []
    for item in raw_toc:
        if len(item) >= 3 and isinstance(item[2], int) and 1 <= item[2] <= total_pages:
            cleaned_toc.append([item[0], item[1].strip() or f"Section at p.{item[2]}", item[2]])

    # 2. Heuristics fallback if raw_toc is empty
    if not cleaned_toc:
        heading_re = re.compile(
            r"^(?:Chapter|Section|Part|\d+(?:\.\d+)*)\b", re.IGNORECASE
        )
        for page_idx in range(total_pages):
            page_num = page_idx + 1
            page_text = doc.load_page(page_idx).get_text("text")
            for line in page_text.splitlines():
                stripped = line.strip()
                if heading_re.match(stripped) and len(stripped) < 80:
                    cleaned_toc.append([1, stripped, page_num])
                    break

    # 3. Uniform chunking fallback if still empty
    if not cleaned_toc:
        start_p = 1
        sec_idx = 1
        while start_p <= total_pages:
            end_p = min(start_p + max_leaf_pages - 1, total_pages)
            cleaned_toc.append([1, f"Section {sec_idx} (Pages {start_p}-{end_p})", start_p])
            start_p = end_p + 1
            sec_idx += 1

    # Insert Front Matter if the first item starts after page 1
    if cleaned_toc[0][2] > 1:
        cleaned_toc.insert(0, [1, "Front Matter & Overview", 1])

    # Calculate end_page for each item
    n_items = len(cleaned_toc)
    items_with_span: list[dict[str, Any]] = []
    for i in range(n_items):
        lvl, title, s_page = cleaned_toc[i]
        # Look ahead for next item with level <= current level
        e_page = total_pages
        for j in range(i + 1, n_items):
            nxt_lvl, _, nxt_s_page = cleaned_toc[j]
            if nxt_lvl <= lvl:
                e_page = max(s_page, nxt_s_page - 1)
                break
            elif j == i + 1:
                # If child starts on a later page, parent end_page at least spans till end
                pass

        # Also clamp to subsequent item's start_page regardless of level if next starts later
        if i + 1 < n_items:
            nxt_s_page = cleaned_toc[i + 1][2]
            if nxt_s_page > s_page and e_page >= nxt_s_page:
                # Parent can span till its sub-sections end, but end_page shouldn't exceed total_pages
                pass

        items_with_span.append({
            "level": lvl,
            "title": title,
            "start_page": s_page,
            "end_page": min(e_page, total_pages),
        })

    # Adjust end_page of each entry so it encompasses its sub-sections
    for i in range(n_items - 1, -1, -1):
        curr = items_with_span[i]
        # Find all direct or deeper children following curr
        max_child_end = curr["end_page"]
        for j in range(i + 1, n_items):
            if items_with_span[j]["level"] <= curr["level"]:
                break
            max_child_end = max(max_child_end, items_with_span[j]["end_page"])
        curr["end_page"] = max_child_end

    # Stack-based hierarchical tree construction
    node_counter = [1]
    stack: list[tuple[dict[str, Any], int, str, int]] = []  # (dict_node, level, struct_prefix, child_index)
    roots_raw: list[dict[str, Any]] = []

    for item in items_with_span:
        lvl = item["level"]
        while stack and stack[-1][1] >= lvl:
            stack.pop()

        if not stack:
            struct = str(len(roots_raw) + 1)
            raw_node = {
                "title": item["title"],
                "level": lvl,
                "start_page": item["start_page"],
                "end_page": item["end_page"],
                "structure": struct,
                "children": [],
            }
            roots_raw.append(raw_node)
            stack.append((raw_node, lvl, struct, len(roots_raw)))
        else:
            parent_node, parent_lvl, parent_struct, _ = stack[-1]
            c_idx = len(parent_node["children"]) + 1
            struct = f"{parent_struct}.{c_idx}"
            raw_node = {
                "title": item["title"],
                "level": lvl,
                "start_page": item["start_page"],
                "end_page": item["end_page"],
                "structure": struct,
                "children": [],
            }
            parent_node["children"].append(raw_node)
            stack.append((raw_node, lvl, struct, c_idx))

    # Helper to convert dict nodes to DocTreeNode, perform large-node split and intro-node insertion
    def _convert_node(raw: dict[str, Any]) -> DocTreeNode:
        title = raw["title"]
        s_page = raw["start_page"]
        e_page = raw["end_page"]
        struct = raw["structure"]
        lvl = raw["level"]

        children_raw = raw.get("children", [])
        converted_children: list[DocTreeNode] = []

        if children_raw:
            # Check for intro pages before first child
            first_child_start = children_raw[0]["start_page"]
            if s_page < first_child_start:
                intro_start = s_page
                intro_end = first_child_start - 1
                intro_node = DocTreeNode(
                    node_id="tmp",
                    structure=f"{struct}.0",
                    title=f"{title} (Intro)",
                    level=lvl + 1,
                    start_page=intro_start,
                    end_page=intro_end,
                    summary="",
                    children=[],
                )
                split_intros = _split_large_leaf(intro_node, max_leaf_pages, node_counter)
                converted_children.extend(split_intros)

            for c_raw in children_raw:
                converted_children.append(_convert_node(c_raw))

            # Recalculate parent end_page to be max of children end_page
            e_page = max(e_page, max(c.end_page for c in converted_children))

            node = DocTreeNode(
                node_id="tmp",
                structure=struct,
                title=title,
                level=lvl,
                start_page=s_page,
                end_page=e_page,
                summary="",
                children=converted_children,
            )
            return node
        else:
            # Leaf node: split if page_span > max_leaf_pages
            temp_node = DocTreeNode(
                node_id="tmp",
                structure=struct,
                title=title,
                level=lvl,
                start_page=s_page,
                end_page=e_page,
                summary="",
                children=[],
            )
            split_leaves = _split_large_leaf(temp_node, max_leaf_pages, node_counter)
            if len(split_leaves) == 1:
                return split_leaves[0]
            else:
                # Converted into a parent with split children
                return DocTreeNode(
                    node_id="tmp",
                    structure=struct,
                    title=title,
                    level=lvl,
                    start_page=s_page,
                    end_page=e_page,
                    summary="",
                    children=split_leaves,
                )

    roots: list[DocTreeNode] = [_convert_node(r) for r in roots_raw]

    # Assign sequential node_ids (0001, 0002, ...) and summaries in pre-order
    counter_id = 1

    def _finalize_node(n: DocTreeNode) -> None:
        nonlocal counter_id
        object.__setattr__(n, "node_id", f"{counter_id:04d}")
        counter_id += 1

        # Extract text preview from start page
        try:
            start_text = doc.load_page(n.start_page - 1).get_text("text")
            summary = sanitize_summary_text(start_text, max_chars=200)
        except Exception:
            summary = ""
        object.__setattr__(n, "summary", summary)

        for c in n.children:
            _finalize_node(c)

    for r in roots:
        _finalize_node(r)

    doc.close()

    tree = DocumentTree(
        doc_id=doc_id,
        total_pages=total_pages,
        max_leaf_pages=max_leaf_pages,
        roots=roots,
    )

    if out_dir is not None:
        out_tree_file = Path(out_dir) / "assets" / doc_id / "tree.json"
        out_tree_file.parent.mkdir(parents=True, exist_ok=True)
        out_tree_file.write_text(tree.model_dump_json(indent=2), encoding="utf-8")

    return tree
