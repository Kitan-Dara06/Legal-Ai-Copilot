"use client";
import { clsx } from "clsx";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "gold" | "ghost" | "danger" | "outline";
  size?: "sm" | "md" | "lg" | "icon";
  loading?: boolean;
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-2 font-medium rounded-lg transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0E0E12] disabled:opacity-40 disabled:cursor-not-allowed select-none";

  const variants = {
    primary:
      "bg-[#7C6AF7] hover:bg-[#6B5AE6] text-white focus-visible:ring-[#7C6AF7] active:scale-[0.98]",
    gold: "bg-[#D4A853] hover:bg-[#C49843] text-[#0E0E12] font-semibold focus-visible:ring-[#D4A853] active:scale-[0.98]",
    ghost:
      "bg-transparent hover:bg-[#1E1E28] text-[#7A7A8A] hover:text-[#F0EEE9] border border-[#2A2A32] hover:border-[#363644] focus-visible:ring-[#7C6AF7]",
    danger:
      "bg-transparent hover:bg-[#F06B6B]/10 text-[#F06B6B] border border-[#F06B6B]/30 hover:border-[#F06B6B]/60 focus-visible:ring-[#F06B6B]",
    outline:
      "bg-transparent border border-[#2A2A32] hover:border-[#7C6AF7] text-[#F0EEE9] hover:text-[#7C6AF7] focus-visible:ring-[#7C6AF7]",
  };

  const sizes = {
    sm: "text-xs px-3 py-1.5 h-7",
    md: "text-sm px-4 py-2 h-9",
    lg: "text-sm px-5 py-2.5 h-11",
    icon: "w-8 h-8 p-0",
  };

  return (
    <button
      className={clsx(base, variants[variant], sizes[size], className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && (
        <span className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
      )}
      {children}
    </button>
  );
}
