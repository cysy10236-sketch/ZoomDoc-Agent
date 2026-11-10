from __future__ import annotations

import operator
from typing import Annotated

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field


class SubState(BaseModel):
    query: str
    internal_crops: list[str] = Field(default_factory=list)
    output_text: str = ""


class CustomCounter(BaseModel):
    count: int = 0
    tokens: int = 0


def merge_counter(a: CustomCounter, b: CustomCounter) -> CustomCounter:
    return CustomCounter(
        count=a.count + b.count,
        tokens=a.tokens + b.tokens,
    )


class ParentState(BaseModel):
    query: str
    items: Annotated[list[str], operator.add] = Field(default_factory=list)
    counter: Annotated[CustomCounter, merge_counter] = Field(default_factory=CustomCounter)
    summary: str = ""


def test_pydantic_reducers_and_subgraph_invocation() -> None:
    # 1. Build subgraph
    sub_builder = StateGraph(SubState)

    def sub_node_1(state: SubState) -> dict:
        return {
            "internal_crops": ["crop_1.png", "crop_2.png"],
            "output_text": f"processed: {state.query}",
        }

    sub_builder.add_node("sub_process", sub_node_1)
    sub_builder.add_edge(START, "sub_process")
    sub_builder.add_edge("sub_process", END)
    compiled_sub = sub_builder.compile()

    # 2. Build parent graph
    parent_builder = StateGraph(ParentState)

    def parent_node_ingest(state: ParentState) -> dict:
        return {
            "items": ["init_item"],
            "counter": CustomCounter(count=1, tokens=50),
        }

    def parent_node_wrap_sub(state: ParentState) -> dict:
        # Invoke subgraph inside node function with mapped input
        sub_input = SubState(query=state.query)
        sub_res = compiled_sub.invoke(sub_input)
        if isinstance(sub_res, dict):
            output_text = sub_res.get("output_text", "")
        else:
            output_text = getattr(sub_res, "output_text", "")

        # Return only mapped fields and increments to parent
        return {
            "items": ["sub_item"],
            "counter": CustomCounter(count=1, tokens=150),
            "summary": output_text,
        }

    parent_builder.add_node("ingest", parent_node_ingest)
    parent_builder.add_node("wrap_sub", parent_node_wrap_sub)
    parent_builder.add_edge(START, "ingest")
    parent_builder.add_edge("ingest", "wrap_sub")
    parent_builder.add_edge("wrap_sub", END)
    compiled_parent = parent_builder.compile()

    init_state = ParentState(query="hello")
    final_output = compiled_parent.invoke(init_state)

    if isinstance(final_output, dict):
        res_state = ParentState.model_validate(final_output)
    else:
        res_state = final_output

    # Verify reducer list addition
    assert res_state.items == ["init_item", "sub_item"]

    # Verify custom reducer aggregation
    assert res_state.counter.count == 2
    assert res_state.counter.tokens == 200

    # Verify subgraph output was mapped
    assert res_state.summary == "processed: hello"

    # Verify parent state has no internal_crops
    assert not hasattr(res_state, "internal_crops")
    dump = res_state.model_dump_json()
    assert "crop_1.png" not in dump
    assert "internal_crops" not in dump
