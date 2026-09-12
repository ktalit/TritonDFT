"""Presentation-neutral, evidence-grounded questions about saved workflows."""
from __future__ import annotations

import os
from pathlib import Path

from generator import UnifiedGenerator
from tool.structural_analysis import call_structural_analysis_tool, format_structural_tool_result
from .evidence_qa import (
    evaluate_calculation,
    evidence_prompt,
    merge_evidence,
    parse_evidence_answer,
    parse_retrieval_plan,
    retrieval_plan_prompt,
    search_workflow_evidence,
    verify_evidence,
    workflow_file_manifest,
    workflow_inventory,
)


def answer_workflow_question(
    run_dir: str | Path,
    question: str,
    *,
    include_failed: bool = False,
) -> tuple[str, str]:
    """Return an answer and UI status using the monitor's verified evidence path."""
    root = Path(run_dir).expanduser().resolve()
    structural = call_structural_analysis_tool(root, question)
    if structural is not None:
        return (
            format_structural_tool_result(structural),
            "Answered by the deterministic pymatgen structural-analysis tool.",
        )

    generator = UnifiedGenerator(
        model=os.environ.get("CLUSTER_AGENT_MODEL", "gpt-4o"),
        backend=os.environ.get("CLUSTER_AGENT_BACKEND", "auto"),
        temperature=0.0,
    )
    inventory = workflow_inventory(root)
    manifest = workflow_file_manifest(root, include_failed=include_failed)
    plan_response = generator(retrieval_plan_prompt(question, inventory, manifest), max_new_tokens=450)
    plan_raw = plan_response[0].get("generated_text", "") if plan_response else ""
    retrieval_queries = parse_retrieval_plan(plan_raw)
    groups = [search_workflow_evidence(root, question, include_failed=include_failed)]
    for retrieval_query in retrieval_queries:
        groups.append(search_workflow_evidence(
            root, retrieval_query, limit=12, include_failed=include_failed
        ))
    evidence = merge_evidence(groups)
    if not evidence:
        return (
            "I could not find relevant evidence in the downloaded workflow files. "
            "This question cannot be answered from the available calculation record.",
            "No supporting lines found.",
        )

    response = generator(evidence_prompt(question, evidence, inventory), max_new_tokens=1100)
    raw = response[0].get("generated_text", "") if response else ""
    parsed = parse_evidence_answer(raw, {item.evidence_id for item in evidence})
    by_id = {item.evidence_id: item for item in evidence}
    cited = [
        by_id[item_id]
        for item_id in parsed["evidence_ids"]
        if verify_evidence(by_id[item_id])
    ]
    if cited:
        output = (
            f"Answer ({parsed['answer_type'].capitalize()})\n{parsed['answer']}\n\n"
            f"Confidence: {parsed['confidence']}\n"
        )
        if parsed["derivation"]:
            output += f"\nDerivation / interpretation\n{parsed['derivation']}\n"
        calculation = parsed["calculation"]
        if calculation["expression"]:
            try:
                calculated = evaluate_calculation(calculation["expression"])
                description = calculation["description"] or "calculated value"
                unit = calculation["unit"]
                output += (
                    f"\nTritonDFT calculated result\n{description}: {calculated:.10g}"
                    f"{(' ' + unit) if unit else ''}\nExpression: {calculation['expression']}\n"
                )
            except (ValueError, ArithmeticError) as exc:
                output += f"\nCalculation was not accepted: {exc}\n"
        output += "\nVerified supporting evidence\n"
    else:
        output = (
            "The model did not provide a verifiable citation, so its proposed answer was not "
            "accepted.\n\nMost relevant verified source lines:\n"
        )
        cited = [item for item in evidence[:5] if verify_evidence(item)]
    for item in cited:
        output += f"\n[{item.evidence_id}] {item.path}:{item.line_number}\n{item.line}\n"
    return output, "Answer completed from workflow-local evidence."
