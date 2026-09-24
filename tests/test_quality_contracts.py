from __future__ import annotations

import operator
import re
from collections import defaultdict, deque
from typing import Annotated, get_args, get_origin, get_type_hints

from app.agent.graph import build_graph
from app.agent.state import AgentState

STABLE_NODE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def _reachable(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        queue.extend(adjacency.get(node, set()) - visited)
    return visited


def test_compiled_graph_has_reachable_nodes_and_exit_paths() -> None:
    graph = build_graph().get_graph()
    adjacency: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        adjacency[edge.source].add(edge.target)
        reverse[edge.target].add(edge.source)

    reachable_from_start = _reachable("__start__", adjacency)
    able_to_reach_end = _reachable("__end__", reverse)
    business_nodes = set(graph.nodes) - {"__start__", "__end__"}

    assert business_nodes <= reachable_from_start
    assert business_nodes <= able_to_reach_end


def test_compiled_graph_does_not_mix_static_and_conditional_outgoing_edges() -> None:
    graph = build_graph().get_graph()
    edge_kinds: dict[str, set[bool]] = defaultdict(set)
    for edge in graph.edges:
        edge_kinds[edge.source].add(edge.conditional)

    assert all(len(kinds) == 1 for kinds in edge_kinds.values())


def test_compiled_graph_uses_stable_low_cardinality_node_names() -> None:
    node_names = set(build_graph().get_graph().nodes) - {"__start__", "__end__"}

    assert node_names
    assert all(STABLE_NODE_NAME.fullmatch(name) for name in node_names)


def test_message_state_keeps_an_accumulating_reducer() -> None:
    annotation = get_type_hints(AgentState, include_extras=True)["messages"]

    assert get_origin(annotation) is Annotated
    assert operator.add in get_args(annotation)[1:]
