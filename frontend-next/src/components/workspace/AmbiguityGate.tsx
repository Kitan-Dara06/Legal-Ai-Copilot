"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

interface AmbiguityGateProps {
  detectedIntent: string;
  confidence: number;
  onConfirm: (intent: string) => void;
}

const INTENT_OPTIONS = ["ANALYZE", "REASON", "ACT"];

export function AmbiguityGate({
  detectedIntent,
  confidence,
  onConfirm,
}: AmbiguityGateProps) {
  const [showOptions, setShowOptions] = useState(false);
  const [selectedIntent, setSelectedIntent] = useState(detectedIntent);

  const intentLabels: Record<string, string> = {
    ANALYZE: "Analyze — Retrieve and summarize relevant clauses",
    REASON: "Reason — Cross-document analysis and contradiction detection",
    ACT: "Act — Draft, approve, and execute legal actions",
  };

  return (
    <Card className="mb-6 p-4 border-blue-500/30">
      <div className="space-y-4">
        <div>
          <h3 className="text-white font-semibold text-lg">
            Confirm Your Intent
          </h3>
          <p className="text-slate-400 text-sm mt-1">
            I detected your goal as:{" "}
            <span className="text-blue-400 font-medium">{detectedIntent}</span>{" "}
            (confidence: {(confidence * 100).toFixed(0)}%)
          </p>
        </div>

        <div className="p-3 bg-slate-800/50 rounded-lg">
          <p className="text-slate-300 text-sm">
            {confidence < 0.8
              ? `I'm not entirely sure about your intent. Based on your request, I think you want me to ${intentLabels[detectedIntent]?.toLowerCase() || "analyze the documents"}.`
              : `I will ${intentLabels[detectedIntent]?.toLowerCase() || "analyze the documents"}.`}
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button onClick={() => onConfirm(selectedIntent)}>Confirm</Button>
          <Button
            variant="outline"
            onClick={() => setShowOptions(!showOptions)}
          >
            Change Intent
          </Button>
        </div>

        {showOptions && (
          <div className="space-y-2 pt-2">
            <p className="text-slate-400 text-xs mb-2">
              Choose a different intent:
            </p>
            {INTENT_OPTIONS.map((intent) => (
              <label
                key={intent}
                className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                  selectedIntent === intent
                    ? "bg-blue-900/30 border border-blue-500/50"
                    : "bg-slate-800/50 border border-transparent hover:border-slate-600"
                }`}
              >
                <input
                  type="radio"
                  name="intent"
                  value={intent}
                  checked={selectedIntent === intent}
                  onChange={(e) => setSelectedIntent(e.target.value)}
                  className="accent-blue-500"
                />
                <div>
                  <span className="text-white text-sm font-medium">
                    {intent}
                  </span>
                  <p className="text-slate-400 text-xs">
                    {intentLabels[intent]}
                  </p>
                </div>
              </label>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
