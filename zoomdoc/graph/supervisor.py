from __future__ import annotations

import functools
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from zoomdoc.agents.base import call_with_schema
from zoomdoc.agents.seeker import Seeker
from zoomdoc.config import Settings, get_settings
from zoomdoc.errors import ZoomDocError
from zoomdoc.graph.critic import critic_node
from zoomdoc.graph.inspector_subgraph import run_inspector_round
from zoomdoc.graph.navigator_subgraph import run_navigator_round
from zoomdoc.graph.states import (
    CriticVerdict,
    EvidenceReport,
    NavigatorReport,
    SupervisorState,
    merge_usage,
    usage_from_sink,
)
from zoomdoc.index.tree import build_document_tree
from zoomdoc.llm.vlm_client import OpenAICompatVLM, TextPart, VLMClient
from zoomdoc.observability import TraceLogger
from zoomdoc.prompts import SYNTHESIZER_TEXT_PROMPT, render_evidence_block
from zoomdoc.schemas import (
    CitedRegion,
    FinalResultM2,
    FinalResultM3,
    StepRecord,
    SynthesizerOutput,
    SynthesizerTextOutput,
    VLMUsage,
)
from zoomdoc.vision.render import get_pdf_page_count, render_pdf, render_pdf_pages

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

NextNode = Literal[
    "tree_builder",
    "navigator",
    "seeker",
    "inspector",
    "critic",
    "synthesizer",
    "finalize",
]


def decide_next(state: SupervisorState, settings: Settings) -> tuple[NextNode, dict[str, Any]]:
    """Pure deterministic decision function for SupervisorState transitions.

    Evaluation order:
    R0: state.result is not None -> finalize
    R1: state.step >= settings.max_steps -> synthesizer if answered evidence else finalize
    R1.5: (total_pages > max_pages or use_tree_index) and doc_tree is None and total_pages > 0 -> tree_builder
    R2a: Initial state before any inspection, candidate_pages non-empty -> seeker
    R2b: Initial state before any inspection, candidate_pages empty:
         if doc_tree is not None and nav_rounds < max_nav_rounds and last_node != "navigator" -> navigator
         else -> finalize
    R2c: Following Navigator execution (last_node == "navigator"):
         if candidate_pages non-empty -> seeker
         else -> synthesizer if answered evidence else finalize
    R3: Following Seeker execution and selected_pages non-empty -> inspector (inspect_rounds += 1)
    R4: Following Seeker execution and selected_pages empty (miss in current section):
         if doc_tree is not None and nav_rounds < max_nav_rounds -> navigator (tree backtracking)
         else -> synthesizer if answered evidence else finalize
    R5: verdict is accepted -> finalize (final_answer = evidence[-1].answer)
    R6: reason_code in {low_confidence, invalid_reference, budget_exhausted} and inspect_rounds < max_inspect_rounds
        -> inspector (inspect_rounds += 1, feedback = verdict.detail)
    R7a: reason_code == need_more_pages and candidates non-empty and seek_rounds < max_seek_rounds
        -> seeker (feedback = evidence[-1].seek_hint) [intra-section re-seek]
    R7b: reason_code == need_more_pages and (candidates empty or seek_rounds >= max_seek_rounds)
         and doc_tree is not None and nav_rounds < max_nav_rounds
        -> navigator (feedback = evidence[-1].seek_hint, candidate_pages = []) [cross-section tree backtracking]
    R8: otherwise -> synthesizer if answered evidence else finalize
    """
    has_answered_evidence = any(e.status == "answered" for e in state.evidence)

    # R0
    if state.result is not None:
        return ("finalize", {})

    # R1
    if state.step >= settings.max_steps:
        next_node = "synthesizer" if has_answered_evidence else "finalize"
        return (next_node, {})

    # Determine last node from history
    last_node = state.history[-1].node if state.history else None

    # R1.5: Tree builder trigger for long documents or when tree index is enabled
    if (
        (state.total_pages > settings.max_pages or settings.use_tree_index)
        and state.doc_tree is None
        and state.total_pages > 0
    ):
        return ("tree_builder", {})

    # R2a: Initial state before any inspection/selection with existing candidate_pages
    if state.verdict is None and not state.evidence and not state.selected_pages:
        if bool(state.candidate_pages):
            return ("seeker", {})
        # R2b: No candidate pages initially
        if (
            state.doc_tree is not None
            and state.nav_rounds < settings.max_nav_rounds
            and last_node != "navigator"
        ):
            return ("navigator", {})
        return ("finalize", {})

    # R2c: Following Navigator execution
    if last_node == "navigator":
        if bool(state.candidate_pages):
            return ("seeker", {})
        else:
            next_node = "synthesizer" if has_answered_evidence else "finalize"
            return (next_node, {})

    # R3 & R4: Following Seeker execution
    if last_node == "seeker":
        if state.selected_pages:
            return ("inspector", {"inspect_rounds": state.inspect_rounds + 1})
        else:
            # Seeker found nothing in the current candidate set
            if state.doc_tree is not None and state.nav_rounds < settings.max_nav_rounds:
                return (
                    "navigator",
                    {
                        "feedback": "Previous section thumbnails did not contain relevant pages; select alternative sections."
                    },
                )
            else:
                next_node = "synthesizer" if has_answered_evidence else "finalize"
                return (next_node, {})

    # R5: Critic accepted verdict
    if state.verdict is not None and state.verdict.accepted:
        final_ans = state.evidence[-1].answer if state.evidence else None
        return ("finalize", {"final_answer": final_ans})

    # R6: Critic rejected with recoverable visual issue
    if (
        state.verdict is not None
        and state.verdict.reason_code in {"low_confidence", "invalid_reference", "budget_exhausted"}
        and state.inspect_rounds < settings.max_inspect_rounds
    ):
        return (
            "inspector",
            {
                "feedback": state.verdict.detail,
                "inspect_rounds": state.inspect_rounds + 1,
            },
        )

    # R7a: Critic requires more pages, intra-section candidate pages remain
    if (
        state.verdict is not None
        and state.verdict.reason_code == "need_more_pages"
        and bool(state.candidate_pages)
        and state.seek_rounds < settings.max_seek_rounds
    ):
        hint = state.evidence[-1].seek_hint if state.evidence else None
        return ("seeker", {"feedback": hint})

    # R7b: Critic requires more pages, candidate pages exhausted -> backtrack to navigator
    if (
        state.verdict is not None
        and state.verdict.reason_code == "need_more_pages"
        and (not state.candidate_pages or state.seek_rounds >= settings.max_seek_rounds)
        and state.doc_tree is not None
        and state.nav_rounds < settings.max_nav_rounds
    ):
        hint = state.evidence[-1].seek_hint if state.evidence else None
        return ("navigator", {"feedback": hint, "candidate_pages": []})

    # R8: Fallback
    next_node = "synthesizer" if has_answered_evidence else "finalize"
    return (next_node, {})


def route_from_supervisor(state: SupervisorState) -> str:
    """Conditional edge router reading state.next."""
    if state.next is None:
        raise ZoomDocError("route_from_supervisor called with state.next is None")
    return state.next


def ingest_node(state: SupervisorState, settings: Settings) -> dict[str, Any]:
    """Ingest PDF: get total page count, and either render (<= 20 p) or defer rendering (long doc)."""
    total_pages = get_pdf_page_count(state.pdf_path)

    if total_pages == 0:
        step_rec = StepRecord(
            step=state.step + 1,
            node="ingest",
            input_images=[],
            output={"pages_count": 0},
            usage=None,
            attempts=1,
        )
        return {
            "total_pages": 0,
            "pages": {},
            "candidate_pages": [],
            "step": state.step + 1,
            "history": [step_rec],
            "render_latency_ms": 0.0,
        }

    if total_pages <= settings.max_pages and not settings.use_tree_index:
        # Milestone 1 & 2 backwards-compatible path: pre-render all pages
        t0 = time.perf_counter()
        pages = render_pdf(state.pdf_path, state.run_dir, settings)
        render_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        pages_dict = {p.page: p for p in pages}
        step_rec = StepRecord(
            step=state.step + 1,
            node="ingest",
            input_images=[p.thumb_path for p in pages],
            output={"pages_count": len(pages)},
            usage=None,
            attempts=1,
        )
        return {
            "total_pages": total_pages,
            "pages": pages_dict,
            "candidate_pages": [p.page for p in pages],
            "step": state.step + 1,
            "history": [step_rec],
            "render_latency_ms": render_ms,
        }
    else:
        # Milestone 3 lazy-rendering path: defer rendering until Navigator selects candidate pages
        step_rec = StepRecord(
            step=state.step + 1,
            node="ingest",
            input_images=[],
            output={"pages_count": total_pages, "mode": "lazy_tree"},
            usage=None,
            attempts=1,
        )
        return {
            "total_pages": total_pages,
            "pages": {},
            "candidate_pages": [],
            "step": state.step + 1,
            "history": [step_rec],
            "render_latency_ms": 0.0,
        }


def tree_builder_node(
    state: SupervisorState,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Build hierarchical outline DocumentTree."""
    t0 = time.perf_counter()
    tree = build_document_tree(
        pdf_path=state.pdf_path,
        max_leaf_pages=settings.max_leaf_pages,
        out_dir=state.run_dir,
    )
    tree_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    step_rec = StepRecord(
        step=state.step + 1,
        node="tree_builder",
        input_images=[],
        output={"total_pages": tree.total_pages, "roots_count": len(tree.roots)},
        usage=None,
        attempts=1,
    )
    return {
        "doc_tree": tree,
        "step": state.step + 1,
        "history": [step_rec],
        "tree_latency_ms": tree_ms,
        "next": None,
    }


def navigator_subgraph_node(
    state: SupervisorState,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Execute Navigator Subgraph in isolated scope and map back candidate pages."""
    if state.doc_tree is None:
        raise ZoomDocError("navigator_subgraph_node called with doc_tree is None")

    report = run_navigator_round(
        question=state.question,
        nav_round=state.nav_rounds + 1,
        doc_tree=state.doc_tree,
        visited_node_ids=state.visited_node_ids,
        feedback=state.feedback,
        trace_id=state.trace_id,
        vlm=vlm,
        settings=settings,
        logger=logger,
    )

    updated_visited = list(dict.fromkeys(state.visited_node_ids + report.selected_node_ids))

    step_rec = StepRecord(
        step=state.step + 1,
        node="navigator",
        input_images=[],
        output=report.model_dump(),
        usage=None,
        attempts=1,
    )

    return {
        "candidate_pages": report.candidate_pages,
        "selected_pages": [],
        "verdict": None,
        "seek_rounds": 0,
        "visited_node_ids": updated_visited,
        "nav_reports": [report],
        "nav_rounds": state.nav_rounds + 1,
        "feedback": None,
        "usage": report.usage,
        "step": state.step + 1,
        "history": [step_rec],
        "next": None,
    }


def supervisor_node(state: SupervisorState, settings: Settings) -> dict[str, Any]:
    """Planner node: executes decide_next and writes target next branch and updates."""
    next_node, updates = decide_next(state, settings)
    res: dict[str, Any] = {"next": next_node}
    res.update(updates)
    return res


def seeker_node(
    state: SupervisorState,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Coarse thumbnail grid selector with on-demand page asset rendering."""
    # Render any candidate pages that have not yet been rendered (lazy on-demand rendering)
    missing_pages = [p for p in state.candidate_pages if p not in state.pages]
    updated_pages = dict(state.pages)
    render_ms = 0.0
    if missing_pages:
        t0 = time.perf_counter()
        rendered = render_pdf_pages(state.pdf_path, missing_pages, state.run_dir, settings)
        render_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        updated_pages.update(rendered)

    candidate_assets = [updated_pages[p] for p in state.candidate_pages]
    seeker = Seeker(vlm, settings, logger)
    sink: list[VLMUsage] = []

    output, selected_pages, resp, attempts = seeker.run(
        question=state.question,
        candidate=candidate_assets,
        feedback=state.feedback,
        out_dir=state.run_dir,
        step=state.seek_rounds + 1,
        usage_sink=sink,
        namespace="main",
    )

    new_candidates = [p for p in state.candidate_pages if p not in selected_pages]
    step_rec = StepRecord(
        step=state.step + 1,
        node="seeker",
        input_images=[p.thumb_path for p in candidate_assets],
        output=output.model_dump(),
        usage=resp.usage,
        attempts=attempts,
    )

    return {
        "pages": updated_pages,
        "candidate_pages": new_candidates,
        "selected_pages": selected_pages,
        "seek_rounds": state.seek_rounds + 1,
        "feedback": None,
        "step": state.step + 1,
        "history": [step_rec],
        "usage": usage_from_sink(sink),
        "render_latency_ms": render_ms,
        "next": None,
    }


def inspector_subgraph_node(
    state: SupervisorState,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Wrapper node executing Inspector Subgraph in an isolated scope.

    Strict contract: returns ONLY the 5 mapped keys:
    {"evidence", "usage", "step", "history", "next"}.
    All intermediate high-res crops and Base64 payloads are discarded inside the sub-graph.
    """
    sub_pages = {p: state.pages[p] for p in state.selected_pages}

    report = run_inspector_round(
        question=state.question,
        round_=state.inspect_rounds,
        pages=sub_pages,
        feedback=state.feedback,
        run_dir=state.run_dir,
        trace_id=state.trace_id,
        vlm=vlm,
        settings=settings,
        logger=logger,
    )

    step_rec = StepRecord(
        step=state.step + 1,
        node="inspector",
        input_images=[],
        output=report.model_dump(),
        usage=None,
        attempts=1,
    )

    return {
        "evidence": [report],
        "usage": report.usage,
        "step": state.step + 1,
        "history": [step_rec],
        "next": None,
    }


def synthesizer_text_node(
    state: SupervisorState,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """Text-only answer synthesizer using accumulated EvidenceReports."""
    evidence_text = render_evidence_block(state.evidence)
    prompt_text = (
        SYNTHESIZER_TEXT_PROMPT.replace("{question}", state.question)
        .replace("{evidence_block}", evidence_text)
    )

    parts = [TextPart(text=prompt_text)]
    sink: list[VLMUsage] = []

    obj, resp, attempts = call_with_schema(
        vlm,
        parts,
        TypeAdapter(SynthesizerTextOutput),
        agent="synthesizer",
        max_attempts=settings.max_attempts,
        logger=logger,
        usage_sink=sink,
        namespace="main",
    )

    all_cited: list[CitedRegion] = []
    for rep in state.evidence:
        all_cited.extend(rep.cited_regions)

    result = FinalResultM3(
        status="synthesized",
        answer=obj.answer,
        reason=obj.reason,
        reference_pages=sorted(list(set(obj.reference_pages))),
        cited_regions=all_cited,
        inspect_rounds=state.inspect_rounds,
        seek_rounds=state.seek_rounds,
        nav_rounds=state.nav_rounds,
        selected_node_ids=state.visited_node_ids,
        total_pages=state.total_pages,
        render_latency_ms=round(state.render_latency_ms, 2),
        tree_latency_ms=round(state.tree_latency_ms, 2),
        total_wall_ms=0.0,
        usage=merge_usage(state.usage, usage_from_sink(sink)),
        trace_id=state.trace_id,
    )

    step_rec = StepRecord(
        step=state.step + 1,
        node="synthesizer",
        input_images=[],
        output=obj.model_dump(),
        usage=resp.usage,
        attempts=attempts,
    )

    return {
        "result": result,
        "final_answer": obj.answer,
        "step": state.step + 1,
        "history": [step_rec],
        "usage": usage_from_sink(sink),
        "next": None,
    }


def finalize_node(state: SupervisorState, settings: Settings) -> dict[str, Any]:
    """Finalize pipeline: write result.json and supervisor_state.json for audit."""
    result = state.result
    if result is None:
        if state.verdict is not None and state.verdict.accepted and state.evidence:
            last_rep = state.evidence[-1]
            result = FinalResultM3(
                status="answered",
                answer=last_rep.answer,
                reason=last_rep.reason,
                reference_pages=last_rep.reference_pages,
                cited_regions=last_rep.cited_regions,
                inspect_rounds=state.inspect_rounds,
                seek_rounds=state.seek_rounds,
                nav_rounds=state.nav_rounds,
                selected_node_ids=state.visited_node_ids,
                total_pages=state.total_pages,
                render_latency_ms=round(state.render_latency_ms, 2),
                tree_latency_ms=round(state.tree_latency_ms, 2),
                total_wall_ms=0.0,
                usage=state.usage,
                trace_id=state.trace_id,
            )
        else:
            has_budget_ex = (
                state.step >= settings.max_steps
                or any(e.status == "budget_exhausted" for e in state.evidence)
                or any(n.status == "budget_exhausted" for n in state.nav_reports)
                or (state.verdict is not None and state.verdict.reason_code == "budget_exhausted")
            )
            status = "budget_exhausted" if has_budget_ex else "no_answer"
            all_cited: list[CitedRegion] = []
            for rep in state.evidence:
                all_cited.extend(rep.cited_regions)

            result = FinalResultM3(
                status=status,
                answer=None,
                reason="Execution completed without accepted answer.",
                reference_pages=[],
                cited_regions=all_cited,
                inspect_rounds=state.inspect_rounds,
                seek_rounds=state.seek_rounds,
                nav_rounds=state.nav_rounds,
                selected_node_ids=state.visited_node_ids,
                total_pages=state.total_pages,
                render_latency_ms=round(state.render_latency_ms, 2),
                tree_latency_ms=round(state.tree_latency_ms, 2),
                total_wall_ms=0.0,
                usage=state.usage,
                trace_id=state.trace_id,
            )

    # Persist result.json
    res_path = Path(state.run_dir) / "result.json"
    res_path.parent.mkdir(parents=True, exist_ok=True)
    with open(res_path, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))

    # Persist supervisor_state.json for isolation verification
    sup_state_path = Path(state.run_dir) / "supervisor_state.json"
    with open(sup_state_path, "w", encoding="utf-8") as f:
        state_dump = state.model_copy(update={"result": result}).model_dump_json(indent=2)
        f.write(state_dump)

    return {"result": result}


def build_supervisor_graph(
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> CompiledStateGraph:
    """Build and compile the Supervisor Master Graph."""
    g = StateGraph(SupervisorState)

    g.add_node("ingest", functools.partial(ingest_node, settings=settings))
    g.add_node("tree_builder", functools.partial(tree_builder_node, settings=settings, logger=logger))
    g.add_node("navigator", functools.partial(navigator_subgraph_node, vlm=vlm, settings=settings, logger=logger))
    g.add_node("supervisor", functools.partial(supervisor_node, settings=settings))
    g.add_node("seeker", functools.partial(seeker_node, vlm=vlm, settings=settings, logger=logger))
    g.add_node("inspector", functools.partial(inspector_subgraph_node, vlm=vlm, settings=settings, logger=logger))
    g.add_node("critic", functools.partial(critic_node, settings=settings, logger=logger))
    g.add_node("synthesizer", functools.partial(synthesizer_text_node, vlm=vlm, settings=settings, logger=logger))
    g.add_node("finalize", functools.partial(finalize_node, settings=settings))

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "supervisor")
    g.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "tree_builder": "tree_builder",
            "navigator": "navigator",
            "seeker": "seeker",
            "inspector": "inspector",
            "critic": "critic",
            "synthesizer": "synthesizer",
            "finalize": "finalize",
        },
    )
    g.add_edge("tree_builder", "supervisor")
    g.add_edge("navigator", "supervisor")
    g.add_edge("seeker", "supervisor")
    g.add_edge("inspector", "critic")
    g.add_edge("critic", "supervisor")
    g.add_edge("synthesizer", "finalize")
    g.add_edge("finalize", END)

    return g.compile()


def run_m2(
    question: str,
    pdf_path: str,
    vlm: VLMClient | None = None,
    settings: Settings | None = None,
    trace_id: str | None = None,
) -> tuple[FinalResultM2, SupervisorState]:
    """Main execution entry point for ZoomDoc Milestone 2 pipeline."""
    cfg = settings or get_settings()
    t_id = trace_id or uuid.uuid4().hex[:12]
    run_dir = Path(cfg.runs_dir) / t_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger = TraceLogger(cfg.runs_dir, t_id)
    client = vlm or OpenAICompatVLM(cfg)

    app = build_supervisor_graph(client, cfg, logger)

    initial_state = SupervisorState(
        trace_id=t_id,
        question=question,
        pdf_path=str(Path(pdf_path).resolve()),
        run_dir=str(run_dir.resolve()),
    )

    limit = 4 * (cfg.max_seek_rounds + cfg.max_inspect_rounds) + 10
    final_output = app.invoke(initial_state, config={"recursion_limit": limit})

    final_state = (
        SupervisorState.model_validate(final_output)
        if isinstance(final_output, dict)
        else final_output
    )

    if final_state.result is None:
        raise ZoomDocError("Pipeline finished without producing a FinalResultM2 object")

    return (final_state.result, final_state)


def run_m3(
    question: str,
    pdf_path: str,
    vlm: VLMClient | None = None,
    settings: Settings | None = None,
    trace_id: str | None = None,
) -> tuple[FinalResultM3, SupervisorState]:
    """Main execution entry point for ZoomDoc Milestone 3 pipeline."""
    t_start = time.perf_counter()
    base_cfg = settings or get_settings()
    cfg = base_cfg.model_copy(
        update={
            "use_tree_index": True,
            "max_steps": max(base_cfg.max_steps, 16),
        }
    )
    t_id = trace_id or uuid.uuid4().hex[:12]
    run_dir = Path(cfg.runs_dir) / t_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger = TraceLogger(cfg.runs_dir, t_id)
    client = vlm or OpenAICompatVLM(cfg)

    app = build_supervisor_graph(client, cfg, logger)

    initial_state = SupervisorState(
        trace_id=t_id,
        question=question,
        pdf_path=str(Path(pdf_path).resolve()),
        run_dir=str(run_dir.resolve()),
    )

    limit = 4 * (cfg.max_seek_rounds + cfg.max_inspect_rounds + cfg.max_nav_rounds) + 15
    final_output = app.invoke(initial_state, config={"recursion_limit": limit})

    final_state = (
        SupervisorState.model_validate(final_output)
        if isinstance(final_output, dict)
        else final_output
    )

    if final_state.result is None:
        raise ZoomDocError("Pipeline finished without producing a FinalResultM3 object")

    wall_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

    # Ensure return object is FinalResultM3 and update total_wall_ms
    res_m3 = (
        final_state.result
        if isinstance(final_state.result, FinalResultM3)
        else FinalResultM3.model_validate(final_state.result.model_dump())
    )
    res_m3 = res_m3.model_copy(update={"total_wall_ms": wall_ms})

    # Re-persist updated result.json
    res_path = Path(final_state.run_dir) / "result.json"
    with open(res_path, "w", encoding="utf-8") as f:
        f.write(res_m3.model_dump_json(indent=2))

    return (res_m3, final_state)

