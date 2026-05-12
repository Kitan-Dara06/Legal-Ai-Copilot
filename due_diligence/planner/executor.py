"""
State Machine Executor (no LangGraph)
======================================
Custom Python state machine managing task execution with explicit transitions:
    PENDING → RUNNING → COMPLETE | ESCALATED

Improvements over the original:
  - Retry: each task retries up to MAX_RETRIES times on exception before escalating
  - Priority sort: tasks are sorted by 'priority' field (1 = highest) before execution
  - 'relevant_documents' field carried through on TaskRecord
  - get_all_results() and get_escalated_tasks() unchanged for downstream compatibility
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

MAX_RETRIES = 2


class TaskState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    ESCALATED = "escalated"


@dataclass
class TaskRecord:
    task_id: int
    tool: str
    search_target: str
    reason: str
    priority: int = 3
    relevant_documents: List[str] = field(default_factory=list)
    state: TaskState = TaskState.PENDING
    result: Any = None
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "tool": self.tool,
            "search_target": self.search_target,
            "reason": self.reason,
            "priority": self.priority,
            "relevant_documents": self.relevant_documents,
            "state": self.state.value,
            "result": self.result,
            "error": self.error,
        }


class StateMachineExecutor:
    """
    Executes a confirmed planner task list through explicit state transitions.
    Tasks are sorted by priority (1 = highest) before execution.

    Supported tools:
      - graph_search   : traverse FalkorDB dependency graph
      - registry_check : look up term definitions + conflicts in PostgreSQL
      - hybrid_search  : query Qdrant dense+sparse (requires retriever + embedder)
    """

    def __init__(
        self, graph, registry, retriever=None, embedder=None, workspace_id: str = ""
    ):
        self.graph = graph
        self.registry = registry
        self.retriever = retriever
        self.embedder = embedder
        self.workspace_id = workspace_id
        self.execution_log: List[TaskRecord] = []

    def execute_plan(self, tasks: List[Dict]) -> List[TaskRecord]:
        """
        Sorts tasks by priority, then runs each through the state machine.
        Returns the full execution log.
        """
        self.execution_log = []

        # Sort by priority ascending (1 = highest priority runs first)
        sorted_tasks = sorted(tasks, key=lambda t: t.get("priority", 3))

        for task_def in sorted_tasks:
            record = TaskRecord(
                task_id=task_def.get("task_id", len(self.execution_log) + 1),
                tool=task_def.get("tool", "unknown"),
                search_target=task_def.get("search_target", ""),
                reason=task_def.get("reason", ""),
                priority=task_def.get("priority", 3),
                relevant_documents=task_def.get("relevant_documents", []),
            )
            self.execution_log.append(record)

            print(
                f"\n[Executor] Task {record.task_id} (priority {record.priority}): "
                f"[{record.tool}] → '{record.search_target}'"
            )
            record.state = TaskState.RUNNING

            # Retry loop
            last_error: Optional[str] = None
            for attempt in range(MAX_RETRIES + 1):
                try:
                    result = self._dispatch(record)
                    record.result = result

                    if not result or (
                        isinstance(result, (list, dict)) and len(result) == 0
                    ):
                        record.state = TaskState.ESCALATED
                        record.error = "No results returned — insufficient coverage."
                        print(f"  ↳ ESCALATED — no results for task {record.task_id}")
                    else:
                        record.state = TaskState.COMPLETE
                        count = len(result) if isinstance(result, (list, dict)) else 1
                        print(f"  ↳ COMPLETE — {count} result(s).")
                    break  # success or empty — don't retry

                except Exception as e:
                    last_error = str(e)
                    if attempt < MAX_RETRIES:
                        print(f"  ↳ RETRY ({attempt + 1}/{MAX_RETRIES}): {e}")
                    else:
                        record.state = TaskState.ESCALATED
                        record.error = last_error
                        print(
                            f"  ↳ ESCALATED — exception after {MAX_RETRIES + 1} attempts: {e}"
                        )

        self._print_summary()
        return self.execution_log

    def _dispatch(self, record: TaskRecord) -> Any:
        """Routes a task record to its tool handler."""
        tool = record.tool
        target = record.search_target

        if tool == "graph_search":
            return self.graph.get_dependency_chains(
                target, workspace_id=self.workspace_id
            )

        elif tool == "registry_check":
            definitions = self.registry.get_term(target)
            conflicts = self.registry.detect_conflicts()
            return {
                "definitions": definitions,
                "conflicts": {
                    k: v for k, v in conflicts.items() if target.lower() in k.lower()
                },
            }

        elif tool == "hybrid_search":
            if self.retriever is None or self.embedder is None:
                raise RuntimeError("hybrid_search requires retriever and embedder.")
            bge_vec = self.embedder.get_bge_query_vector(target)
            lb_vec = self.embedder.get_legal_bert_query_vector(target)
            splade_vec = self.embedder.get_splade_query_vector(target)
            return self.retriever.search(target, bge_vec, lb_vec, splade_vec, limit=5)

        else:
            raise ValueError(
                f"Unknown tool: '{tool}'. Supported: graph_search, registry_check, hybrid_search."
            )

    def _print_summary(self):
        total = len(self.execution_log)
        complete = sum(1 for r in self.execution_log if r.state == TaskState.COMPLETE)
        escalated = sum(1 for r in self.execution_log if r.state == TaskState.ESCALATED)
        print(
            f"\n[Executor] Plan complete — {complete}/{total} COMPLETE, "
            f"{escalated}/{total} ESCALATED."
        )

    def get_all_results(self) -> Dict[str, Any]:
        return {
            f"task_{r.task_id}": r.result
            for r in self.execution_log
            if r.state == TaskState.COMPLETE
        }

    def get_escalated_tasks(self) -> List[TaskRecord]:
        return [r for r in self.execution_log if r.state == TaskState.ESCALATED]
