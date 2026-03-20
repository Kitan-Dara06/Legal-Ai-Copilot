import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import { type ChatMessage as ChatMessageType } from "@/lib/types";
import { Scale, User as UserIcon } from "lucide-react";

interface ChatMessageProps {
  message: ChatMessageType;
  isLast?: boolean;
}

export function ChatMessage({ message, isLast }: ChatMessageProps) {
  const isAi = message.role === "assistant";

  return (
    <div
      className={cn(
        "flex w-full px-4 py-8 md:px-6 lg:px-8",
        isAi ? "bg-navy-950/50" : "bg-transparent"
      )}
    >
      <div className="flex w-full max-w-4xl mx-auto gap-4 md:gap-6">
        {/* Avatar */}
        <div className="flex-shrink-0 mt-1">
          {isAi ? (
            <div className="w-8 h-8 rounded-lg bg-accent-gold/20 border border-accent-gold/30 flex items-center justify-center">
              <Scale className="w-5 h-5 text-accent-gold" strokeWidth={2} />
            </div>
          ) : (
            <div className="w-8 h-8 rounded-lg bg-accent-blue/20 border border-accent-blue/30 flex items-center justify-center">
              <UserIcon className="w-5 h-5 text-accent-blue" strokeWidth={2} />
            </div>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 space-y-2 overflow-hidden w-full min-w-0">
          <div className="font-semibold text-sm text-slate-400">
            {isAi ? "Legal AI Copilot" : "You"}
          </div>
          <div className={cn(
            "prose prose-invert max-w-full break-words overflow-x-auto",
            "prose-p:leading-relaxed prose-pre:bg-navy-900 prose-pre:border prose-pre:border-slate-800",
            "prose-a:text-accent-blue prose-a:no-underline hover:prose-a:underline",
            "prose-strong:text-white prose-code:text-accent-gold prose-code:bg-accent-gold/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded",
            "prose-table:w-full prose-table:overflow-x-auto",
            "scrollbar-thin scrollbar-thumb-slate-700 scrollbar-track-transparent",
            isAi ? "text-slate-300" : "text-white text-lg" // specific styling requested in the mockup
          )}>
            {isAi ? (
              <ReactMarkdown>{message.content}</ReactMarkdown>
            ) : (
              <p className="whitespace-pre-wrap">{message.content}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
