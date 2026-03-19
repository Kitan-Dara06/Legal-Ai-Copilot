import { useEffect, useRef } from "react";
import { ChatMessage } from "./ChatMessage";
import { type ChatMessage as ChatMessageType } from "@/lib/types";
import { Scale } from "lucide-react";

interface ChatThreadProps {
  messages: ChatMessageType[];
  isLoading?: boolean;
}

export function ChatThread({ messages, isLoading }: ChatThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center h-full">
        <div className="w-16 h-16 rounded-2xl bg-accent-blue/10 border border-accent-blue/20 flex items-center justify-center mb-6 shadow-[0_0_30px_rgba(59,130,246,0.15)]">
          <Scale className="w-8 h-8 text-accent-blue" strokeWidth={1.5} />
        </div>
        <h2 className="text-2xl font-bold text-white mb-2">Legal AI Copilot</h2>
        <p className="text-slate-400 max-w-md">
          Create a workspace session from your documents library and ask questions about your contracts.
        </p>
      </div>
    );
  }

  return (
    <div className="w-full h-full overflow-y-auto pb-32">
      {messages.map((msg, idx) => (
        <ChatMessage 
          key={idx} 
          message={msg} 
          isLast={idx === messages.length - 1} 
        />
      ))}
      
      {isLoading && (
        <div className="flex w-full px-4 py-8 md:px-6 lg:px-8 bg-navy-950/50">
          <div className="flex w-full max-w-4xl mx-auto gap-4 md:gap-6">
            <div className="flex-shrink-0 mt-1">
              <div className="w-8 h-8 rounded-lg bg-accent-gold/20 border border-accent-gold/30 flex items-center justify-center">
                <Scale className="w-5 h-5 text-accent-gold animate-pulse" strokeWidth={2} />
              </div>
            </div>
            <div className="flex-1 space-y-2">
              <div className="font-semibold text-sm text-slate-400">Legal AI Copilot</div>
              <div className="flex gap-1 pt-2">
                <div className="w-2 h-2 bg-slate-500 rounded-full animate-bounce [animation-delay:-0.3s]"></div>
                <div className="w-2 h-2 bg-slate-500 rounded-full animate-bounce [animation-delay:-0.15s]"></div>
                <div className="w-2 h-2 bg-slate-500 rounded-full animate-bounce"></div>
              </div>
            </div>
          </div>
        </div>
      )}
      
      <div ref={bottomRef} className="h-4" />
    </div>
  );
}
