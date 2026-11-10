from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, Literal

from langgraph.graph import END, START, StateGraph

from zoomdoc.agents.navigator import Navigator
from zoomdoc.errors import ZoomDocError
from zoomdoc.graph.states import (
    NavigatorReport,
    NavigatorSubState,
    merge_usage,
    usage_from_sink,
)
from zoomdoc.index.tree import collect_candidate_pages, flatten_tree_nodes
from zoomdoc.schemas import DocTreeNode, DocumentTree, VLMUsage

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from zoomdoc.config import Settings
    from zoomdoc.llm.vlm_client import VLMClient
    from zoomdoc.observability import TraceLogger


def _is_fully_visited(node: DocTreeNode, visited_set: set[str]) -> bool:
    if not node.children:
        return node.node_id in visited_set
    return all(_is_fully_visited(c, visited_set) for c in node.children)


def nav_prepare_node(state: NavigatorSubState) -> dict[str, Any]:
    """Prepare current_frontier for the next navigation decision step."""
    all_nodes = flatten_tree_nodes(state.doc_tree)
    visited_set = set(state.visited_node_ids)

    if not state.current_frontier:
        # Initial frontier: unvisited root nodes
        roots_unvisited = [
            r.node_id for r in state.doc_tree.roots if not _is_fully_visited(r, visited_set)
        ]
        return {"current_frontier": roots_unvisited}

    # Filter out any fully visited nodes in the existing frontier
    active = [
        nid
        for nid in state.current_frontier
        if nid in all_nodes and not _is_fully_visited(all_nodes[nid], visited_set)
    ]
    return {"current_frontier": active}


def nav_step_node(
    state: NavigatorSubState,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Execute one navigation reasoning step via pure-text LLM/VLM."""
    all_nodes = flatten_tree_nodes(state.doc_tree)
    visited_set = set(state.visited_node_ids)

    frontier_nodes = [
        all_nodes[nid]
        for nid in state.current_frontier
        if nid in all_nodes and not _is_fully_visited(all_nodes[nid], visited_set)
    ]

    if not frontier_nodes:
        return {"sub_step": state.sub_step + 1}

    can_expand = (state.sub_step + 1 < state.max_nav_steps) and any(
        bool(n.children) for n in frontier_nodes
    )

    navigator = Navigator(vlm, settings, logger)
    sink: list[VLMUsage] = []

    output, resp, attempts = navigator.run(
        question=state.question,
        frontier_nodes=frontier_nodes,
        visited_node_ids=visited_set,
        feedback=state.feedback,
        can_expand=can_expand,
        usage_sink=sink,
        namespace=f"navigator_sub/r{state.nav_round}",
    )

    step_usage = usage_from_sink(sink)
    new_usage = merge_usage(state.usage, step_usage)

    if output.action == "select":
        return {
            "selected_node_ids": output.node_ids,
            "last_reason": output.reason,
            "sub_step": state.sub_step + 1,
            "usage": new_usage,
        }
    else:
        # action == "expand": gather children of selected parent nodes
        new_frontier: list[str] = []
        for nid in output.node_ids:
            parent_node = all_nodes[nid]
            for child in parent_node.children:
                if not _is_fully_visited(child, visited_set) and child.node_id not in new_frontier:
                    new_frontier.append(child.node_id)

        return {
            "current_frontier": new_frontier,
            "last_reason": output.reason,
            "sub_step": state.sub_step + 1,
            "usage": new_usage,
        }


def route_nav(state: NavigatorSubState) -> Literal["nav_prepare", "nav_pack_report"]:
    """Conditional router for Navigator Subgraph."""
    if (
        not state.current_frontier
        or bool(state.selected_node_ids)
        or state.sub_step >= state.max_nav_steps
    ):
        return "nav_pack_report"
    return "nav_prepare"


def nav_pack_report_node(state: NavigatorSubState) -> dict[str, Any]:
    """Package navigation results into a strict NavigatorReport."""
    if state.selected_node_ids:
        pages, newly_visited = collect_candidate_pages(
            tree=state.doc_tree,
            selected_node_ids=state.selected_node_ids,
            visited_node_ids=state.visited_node_ids,
            max_pages=state.max_candidate_pages,
        )
        status = "located" if pages else "exhausted"
        reason = state.last_reason or "Selected target sections for visual inspection."
        report = NavigatorReport(
            nav_round=state.nav_round,
            status=status,
            selected_node_ids=state.selected_node_ids,
            candidate_pages=pages,
            reason=reason,
            sub_steps=state.sub_step,
            usage=state.usage,
        )
        return {"report": report}

    # If selected_node_ids is empty but step budget was exhausted, fallback to current_frontier
    if state.current_frontier:
        pages, newly_visited = collect_candidate_pages(
            tree=state.doc_tree,
            selected_node_ids=state.current_frontier,
            visited_node_ids=state.visited_node_ids,
            max_pages=state.max_candidate_pages,
        )
        status = "located" if pages else "budget_exhausted"
        reason = state.last_reason or "Navigation step budget exhausted; fallback to frontier."
        report = NavigatorReport(
            nav_round=state.nav_round,
            status=status,
            selected_node_ids=state.current_frontier,
            candidate_pages=pages,
            reason=reason,
            sub_steps=state.sub_step,
            usage=state.usage,
        )
        return {"report": report}

    # All nodes exhausted
    report = NavigatorReport(
        nav_round=state.nav_round,
        status="exhausted",
        selected_node_ids=[],
        candidate_pages=[],
        reason="All reachable outline nodes have been visited. No candidates remaining.",
        sub_steps=state.sub_step,
        usage=state.usage,
    )
    return {"report": report}


def build_navigator_subgraph(
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> CompiledStateGraph:
    """Build and compile isolated Navigator Subgraph."""
    g = StateGraph(NavigatorSubState)

    g.add_node("nav_prepare", nav_prepare_node)
    g.add_node(
        "nav_step",
        functools.partial(nav_step_node, vlm=vlm, settings=settings, logger=logger),
    )
    g.add_node("nav_pack_report", nav_pack_report_node)

    g.add_edge(START, "nav_prepare")
    g.add_edge("nav_prepare", "nav_step")
    g.add_conditional_edges(
        "nav_step",
        route_nav,
        {
            "nav_prepare": "nav_prepare",
            "nav_pack_report": "nav_pack_report",
        },
    )
    g.add_edge("nav_pack_report", END)

    return g.compile()


def run_navigator_round(
    *,
    question: str,
    nav_round: int,
    doc_tree: DocumentTree,
    visited_node_ids: list[str],
    feedback: str | None,
    trace_id: str,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> NavigatorReport:
    """Execute one isolated navigator round and return strict NavigatorReport."""
    subgraph = build_navigator_subgraph(vlm, settings, logger)
    initial_state = NavigatorSubState(
        trace_id=trace_id,
        question=question,
        nav_round=nav_round,
        doc_tree=doc_tree,
        visited_node_ids=visited_node_ids,
        feedback=feedback,
        max_nav_steps=settings.max_nav_steps,
        max_candidate_pages=settings.max_pages,
    )

    limit = 2 * settings.max_nav_steps + 10
    final_output = subgraph.invoke(initial_state, config={"recursion_limit": limit})

    report = (
        final_output.get("report")
        if isinstance(final_output, dict)
        else getattr(final_output, "report", None)
    )

    if report is None:
        raise ZoomDocError("Navigator subgraph finished without producing a NavigatorReport")

    return report
