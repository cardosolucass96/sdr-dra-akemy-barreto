from langgraph.graph import END, START, StateGraph

from app.agent.context import AgentRunContext
from app.agent.nodes import (
    classify_intent,
    delegate_specialist,
    interpret_liana_progress,
    plan_liana_pipefacil_sync,
    respond,
    sync_liana_pipefacil_deal,
)
from app.agent.routing import (
    CLASSIFY_INTENT_NODE,
    DELEGATE_SPECIALIST_NODE,
    INTERPRET_LIANA_PROGRESS_NODE,
    PLAN_LIANA_SYNC_NODE,
    RESPOND_NODE,
    SYNC_LIANA_DEAL_NODE,
    route_after_intent,
    route_after_liana_plan,
)
from app.agent.state import AgentState


def build_graph(*, checkpointer=None):
    builder = StateGraph(AgentState, context_schema=AgentRunContext)
    builder.add_node(CLASSIFY_INTENT_NODE, classify_intent)
    builder.add_node(DELEGATE_SPECIALIST_NODE, delegate_specialist)
    builder.add_node(INTERPRET_LIANA_PROGRESS_NODE, interpret_liana_progress)
    builder.add_node(PLAN_LIANA_SYNC_NODE, plan_liana_pipefacil_sync)
    builder.add_node(SYNC_LIANA_DEAL_NODE, sync_liana_pipefacil_deal)
    builder.add_node(RESPOND_NODE, respond)
    builder.add_edge(START, CLASSIFY_INTENT_NODE)
    builder.add_conditional_edges(
        CLASSIFY_INTENT_NODE,
        route_after_intent,
        {
            DELEGATE_SPECIALIST_NODE: DELEGATE_SPECIALIST_NODE,
            INTERPRET_LIANA_PROGRESS_NODE: INTERPRET_LIANA_PROGRESS_NODE,
        },
    )
    builder.add_edge(DELEGATE_SPECIALIST_NODE, INTERPRET_LIANA_PROGRESS_NODE)
    builder.add_edge(INTERPRET_LIANA_PROGRESS_NODE, PLAN_LIANA_SYNC_NODE)
    builder.add_conditional_edges(
        PLAN_LIANA_SYNC_NODE,
        route_after_liana_plan,
        {
            SYNC_LIANA_DEAL_NODE: SYNC_LIANA_DEAL_NODE,
            RESPOND_NODE: RESPOND_NODE,
        },
    )
    builder.add_edge(SYNC_LIANA_DEAL_NODE, RESPOND_NODE)
    builder.add_edge(RESPOND_NODE, END)
    compile_kwargs = {"checkpointer": checkpointer} if checkpointer is not None else {}
    return builder.compile(**compile_kwargs)


graph = build_graph()
