"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

interface DraftPreviewProps {
    title?: string;
    content: string;
    citations?: string[];
    onDownload?: () => void;
}

export function DraftPreview({ title, content, citations, onDownload }: DraftPreviewProps) {
    const [expanded, setExpanded] = useState(false);

    const handleDownloadDocx = () => {
        if (onDownload) {
            onDownload();
            return;
        }

        // Fallback: generate a simple .docx-compatible HTML file
        const html = `<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>${title || "Legal Draft"}</title></head>
<body style="font-family: 'Times New Roman', serif; max-width: 800px; margin: 40px auto; line-height: 1.6;">
    <h1>${title || "Legal Draft"}</h1>
    <hr/>
    <div>${content.replace(/\n/g, "<br/>")}</div>
    ${citations && citations.length ? `
    <hr/>
    <h2>Sources</h2>
    <ul>${citations.map(c => `<li>${c}</li>`).join("")}</ul>
    ` : ""}
</body>
</html>`;

        const blob = new Blob([html], { type: "application/msword" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${title || "legal_draft"}.doc`;
        a.click();
        URL.revokeObjectURL(url);
    };

    return (
        <Card className="mb-6 p-4">
            <div className="flex items-center justify-between mb-4">
                <h3 className="text-white font-semibold text-lg">
                    {title || "Draft Preview"}
                </h3>
                <Button size="sm" onClick={handleDownloadDocx}>
                    Download .DOCX
                </Button>
            </div>

            {/* Markdown Rendered Content */}
            <div className="prose prose-invert max-w-none text-slate-300 text-sm leading-relaxed whitespace-pre-wrap font-serif">
                {content}
            </div>

            {/* Citations */}
            {citations && citations.length > 0 && (
                <div className="mt-4 pt-4 border-t border-slate-700">
                    <button
                        className="text-blue-400 text-sm hover:text-blue-300 transition-colors"
                        onClick={() => setExpanded(!expanded)}
                    >
                        {expanded ? "Hide" : "Show"} citations ({citations.length})
                    </button>
                    {expanded && (
                        <div className="mt-2 space-y-1">
                            {citations.map((c, i) => (
                                <div key={i} className="text-slate-500 text-xs">
                                    [{i + 1}] {c}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </Card>
    );
}
