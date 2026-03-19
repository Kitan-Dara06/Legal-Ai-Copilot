import { cn } from "@/lib/utils";
import React from "react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "outline" | "ghost" | "danger" | "nav";
  size?: "default" | "sm" | "lg" | "icon";
  isLoading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", isLoading, children, disabled, ...props }, ref) => {
    return (
      <button
        ref={ref}
        disabled={disabled || isLoading}
        className={cn(
          "inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none ring-offset-navy-950",
          {
            "bg-accent-blue text-white hover:bg-accent-blue-dark active:scale-95 transition-transform": variant === "default",
            "border border-slate-700 hover:bg-slate-800 text-slate-200": variant === "outline",
            "hover:bg-slate-800/50 text-slate-300 hover:text-white": variant === "ghost",
            "bg-red-500/10 text-red-500 hover:bg-red-500/20 border border-transparent hover:border-red-500/30": variant === "danger",
            "w-full justify-start gap-3 px-3 py-2 text-slate-400 hover:text-white hover:bg-white/5 rounded-lg": variant === "nav",
            
            "h-10 py-2 px-4": size === "default" && variant !== "nav",
            "h-9 px-3": size === "sm" && variant !== "nav",
            "h-11 px-8": size === "lg",
            "h-10 w-10": size === "icon",
          },
          className
        )}
        {...props}
      >
        {isLoading && (
          <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-current" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
        )}
        {children}
      </button>
    );
  }
);
Button.displayName = "Button";
