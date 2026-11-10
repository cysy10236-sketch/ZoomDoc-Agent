from __future__ import annotations

import functools
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from zoomdoc.config import Settings, get_settings
from zoomdoc.graph.nodes import (
    finalize_node,
    ingest_node,
    inspector_node,
    route_after_ingest,
    route_after_inspector,
    route_after_seeker,
    seeker_node,
    synthesizer_node,
    zoom_node,
)
from zoomdoc.graph.state import AgentState
from zoomdoc.llm.vlm_client import OpenAICompatVLM, VLMClient
from zoomdoc.observability import TraceLogger
from zoomdoc.schemas import FinalResult


def build_graph(vlm: VLMClient, settings: Settings, logger: TraceLogger) -> Any:
    """Build and compile the LangGraph state machine for Milestone 1."""
    g = StateGraph(AgentState)

    g.add_node("ingest", functools.partial(ingest_node, settings=settings))
    g.add_node("seeker", functools.partial(seeker_node, vlm=vlm, settings=settings, logger=logger))
    g.add_node("inspector", functools.partial(inspector_node, vlm=vlm, settings=settings, logger=logger))
    g.add_node("zoom", functools.partial(zoom_node, settings=settings, logger=logger))
    g.add_node("synthesizer", functools.partial(synthesizer_node, vlm=vlm, settings=settings, logger=logger))
    g.add_node("finalize", functools.partial(finalize_node, settings=settings))

    g.add_edge(START, "ingest")
    g.add_conditional_edges(
        "ingest",
        route_after_ingest,
        {"seeker": "seeker", "finalize": "finalize"},
    )
    g.add_conditional_edges(
        "seeker",
        route_after_seeker,
        {"inspector": "inspector", "synthesizer": "synthesizer", "finalize": "finalize"},
    )
    g.add_conditional_edges(
        "inspector",
        functools.partial(route_after_inspector, settings=settings),
        {"seeker": "seeker", "zoom": "zoom", "synthesizer": "synthesizer", "finalize": "finalize"},
    )
    g.add_edge("zoom", "inspector")
    g.add_edge("synthesizer", "finalize")
    g.add_edge("finalize", END)

    return g.compile()


def run(
    question: str,
    pdf_path: str,
    vlm: VLMClient | None = None,
    settings: Settings | None = None,
    trace_id: str | None = None,
) -> FinalResult:
    """Main execution entry point for ZoomDoc Milestone 1 pipeline."""
    cfg = settings or get_settings()
    t_id = trace_id or uuid.uuid4().hex[:12]
    run_dir = Path(cfg.runs_dir) / t_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger = TraceLogger(cfg.runs_dir, t_id)
    client = vlm or OpenAICompatVLM(cfg)

    app = build_graph(client, cfg, logger)

    initial_state = AgentState(
        trace_id=t_id,
        question=question,
        pdf_path=str(Path(pdf_path).resolve()),
        run_dir=str(run_dir.resolve()),
    )

    limit = cfg.max_steps * 2 + 10
    final_output = app.invoke(initial_state, config={"recursion_limit": limit})

    result = final_output.get("result")
    if result is None:
        raise RuntimeError("Pipeline finished without producing a FinalResult object")

    return result
