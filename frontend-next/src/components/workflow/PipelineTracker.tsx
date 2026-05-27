"use client";

import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { clsx } from "clsx";
import type { WorkflowStatus } from "@/lib/types";

type Stage = "brief" | "draft" | "export";

interface PipelineTrackerProps {
  status: WorkflowStatus | null;
}

function getStage(status: WorkflowStatus | null): {
  active: Stage | null;
  completed: Stage[];
} {
  if (!status) return { active: null, completed: [] };

  const briefStatuses: WorkflowStatus[] = [
    "CLASSIFYING", "AWAITING_INTENT_CONFIRMATION",
    "RETRIEVING", "EXPANDING", "REASONING",
    "BRIEFING", "AWAITING_BRIEF_CONFIRMATION",
  ];
  const draftStatuses: WorkflowStatus[] = ["DRAFTING", "AWAITING_APPROVAL", "REVISING"];
  const exportStatuses: WorkflowStatus[] = ["COMPLETED"];

  if (briefStatuses.includes(status)) return { active: "brief", completed: [] };
  if (draftStatuses.includes(status)) return { active: "draft", completed: ["brief"] };
  if (exportStatuses.includes(status)) return { active: null, completed: ["brief", "draft", "export"] };

  return { active: null, completed: [] };
}

const STAGES: { id: Stage; label: string; description: string }[] = [
  { id: "brief",  label: "Decision Brief",    description: "Analysis & action plan" },
  { id: "draft",  label: "Draft Generation",  description: "Grounded document draft" },
  { id: "export", label: "Review & Export",   description: "Approve and download DOCX" },
];

export function PipelineTracker({ status }: PipelineTrackerProps) {
  const { active, completed } = getStage(status);

  return (
    <div className="w-full flex items-start gap-0 mb-8">
      {STAGES.map((stage, idx) => {
        const isDone   = completed.includes(stage.id);
        const isActive = active === stage.id;
        const isLast   = idx === STAGES.length - 1;

        return (
          <div key={stage.id} className="flex-1 flex items-start">
            {/* Step content */}
            <div className="flex flex-col items-center flex-1">
              {/* Circle + connector */}
              <div className="flex items-center w-full">
                {/* Left connector */}
                {idx > 0 && (
                  <div
                    className={clsx(
                      "flex-1 h-[2px] transition-colors duration-500",
                      completed.includes(STAGES[idx - 1].id) ? "bg-[#D4A853]" : "bg-[#2A2A32]",
                    )}
                  />
                )}

                {/* Icon */}
                <div
                  className={clsx(
                    "w-8 h-8 rounded-full flex items-center justify-center shrink-0 transition-all duration-300",
                    isDone && "bg-[#D4A853] text-[#0E0E12]",
                    isActive && "bg-[#D4A853]/15 border-2 border-[#D4A853] text-[#D4A853] animate-pulse-gold",
                    !isDone && !isActive && "bg-[#16161D] border-2 border-[#2A2A32] text-[#4A4A5A]",
                  )}
                >
                  {isDone ? (
                    <CheckCircle2 className="w-4 h-4" />
                  ) : isActive ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Circle className="w-3.5 h-3.5" />
                  )}
                </div>

                {/* Right connector */}
                {!isLast && (
                  <div
                    className={clsx(
                      "flex-1 h-[2px] transition-colors duration-500",
                      isDone ? "bg-[#D4A853]" : "bg-[#2A2A32]",
                    )}
                  />
                )}
              </div>

              {/* Labels */}
              <div className="mt-2 text-center px-1">
                <p
                  className={clsx(
                    "text-xs font-semibold transition-colors",
                    isDone  && "text-[#D4A853]",
                    isActive && "text-[#F0EEE9]",
                    !isDone && !isActive && "text-[#4A4A5A]",
                  )}
                >
                  {stage.label}
                </p>
                <p className="text-[10px] text-[#7A7A8A] mt-0.5 leading-tight">
                  {stage.description}
                </p>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
