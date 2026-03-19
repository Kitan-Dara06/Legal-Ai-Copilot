"use client";

import { useState, useRef, useEffect } from "react";
import { Send, Sparkles } from "lucide-react";
import { Button } from "../ui/Button";

interface ChatInputProps {
  onSend: (message: string) => void;
  isLoading: boolean;
}

export function ChatInput({ onSend, isLoading }: ChatInputProps) {
  const [message, setMessage] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [message]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (message.trim() && !isLoading) {
      onSend(message.trim());
      setMessage("");
      // Reset height
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="relative flex items-end w-full max-w-4xl mx-auto bg-slate-900/80 rounded-2xl border border-slate-700/50 shadow-lg focus-within:border-accent-blue/50 focus-within:ring-1 focus-within:ring-accent-blue/50 transition-all"
    >
      <textarea
        ref={textareaRef}
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask a question about your contracts..."
        disabled={isLoading}
        rows={1}
        className="w-full max-h-[200px] py-4 pl-4 pr-12 bg-transparent border-none focus:ring-0 resize-none text-slate-100 placeholder-slate-500 disabled:opacity-50"
      />
      <div className="absolute right-2 bottom-2">
        <Button
          type="submit"
          size="icon"
          disabled={!message.trim() || isLoading}
          className="h-10 w-10 shrink-0 rounded-xl"
        >
          {isLoading ? (
            <Sparkles className="h-5 w-5 animate-pulse" />
          ) : (
            <Send className="h-5 w-5" />
          )}
        </Button>
      </div>
    </form>
  );
}
