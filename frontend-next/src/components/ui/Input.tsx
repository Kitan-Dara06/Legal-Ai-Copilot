import { cn } from "@/lib/utils";
import React from "react";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  error?: string;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, error, ...props }, ref) => {
    return (
      <div className="w-full">
        <input
          className={cn(
            "flex h-10 w-full rounded-md border text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-slate-400 disabled:cursor-not-allowed disabled:opacity-50",
            "glass-input focus:glass-input-focus px-3 py-2 text-slate-100",
            error ? "border-red-500 focus:border-red-500 focus:ring-red-500" : "border-slate-800",
            className
          )}
          ref={ref}
          {...props}
        />
        {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
      </div>
    );
  }
);
Input.displayName = "Input";
