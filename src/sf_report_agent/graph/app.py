from __future__ import annotations

from itertools import pairwise

from langgraph.graph import END, START, StateGraph

from sf_report_agent.graph.nodes import AgentServices, ReportGraphNodes
from sf_report_agent.graph.state import ReportAgentState
from sf_report_agent.models.execution_result import ExecutionResult


def build_graph(services: AgentServices):  # type: ignore[no-untyped-def]
    nodes = ReportGraphNodes(services)
    builder = StateGraph(ReportAgentState)
    ordered_nodes = [
        ("load_task", nodes.load_task),
        ("parse_request", nodes.parse_request),
        ("resolve_salesforce_schema", nodes.resolve_salesforce_schema),
        ("check_permissions", nodes.check_permissions),
        ("build_report_plan", nodes.build_report_plan),
        ("validate_plan", nodes.validate_plan),
        ("build_soql", nodes.build_soql),
        ("validate_soql", nodes.validate_soql),
        ("execute_query", nodes.execute_query),
        ("transform_dataset", nodes.transform_dataset),
        ("quality_checks", nodes.quality_checks),
        ("export_report", nodes.export_report),
        ("compose_response", nodes.compose_response),
        ("persist_result", nodes.persist_result),
    ]
    for name, node in ordered_nodes:
        builder.add_node(name, node)
    builder.add_edge(START, ordered_nodes[0][0])
    for current, following in pairwise(ordered_nodes):
        builder.add_edge(current[0], following[0])
    builder.add_edge(ordered_nodes[-1][0], END)
    return builder.compile()


class ReportAgentRunner:
    def __init__(self, services: AgentServices) -> None:
        self.services = services
        self.graph = build_graph(services)

    def run(self, task_id: int, *, dry_run: bool = False) -> ExecutionResult:
        run_id = self.services.run_repository.start_run(task_id)
        try:
            final_state = self.graph.invoke(
                {
                    "task_id": task_id,
                    "dry_run": dry_run,
                    "run_id": run_id,
                    "errors": [],
                    "warnings": [],
                    "artifacts": [],
                    "status": "started",
                }
            )
        except Exception as exc:
            self.services.run_repository.finish_run(run_id, status="failed", error=str(exc))
            raise
        return ExecutionResult(
            task_id=task_id,
            status=final_state["status"],
            row_count=int(final_state.get("quality_report", {}).get("row_count", 0)),
            artifacts=final_state.get("artifacts", []),
            response_text=final_state.get("response_text", ""),
            warnings=final_state.get("warnings", []),
            errors=final_state.get("errors", []),
        )
