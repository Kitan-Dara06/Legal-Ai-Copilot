import { cn } from "@/lib/utils";
import React from "react";

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "success" | "warning" | "error" | "info";
}

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
        {
          "border-transparent bg-slate-800 text-slate-100": variant === "default",
          "border-transparent bg-emerald-500/15 text-emerald-400": variant === "success",
          "border-transparent bg-amber-500/15 text-amber-500": variant === "warning",
          "border-transparent bg-red-500/15 text-red-400": variant === "error",
          "border-transparent bg-blue-500/15 text-blue-400": variant === "info",
        },
        className
      )}
      {...props}
    />
  );
}
