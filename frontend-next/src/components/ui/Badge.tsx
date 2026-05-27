"use client";
import { clsx } from "clsx";

interface BadgeProps {
  variant?: "default" | "gold" | "violet" | "success" | "warning" | "danger" | "muted";
  children: React.ReactNode;
  className?: string;
}

export function Badge({ variant = "default", children, className }: BadgeProps) {
  const base = "inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full";
  const variants = {
    default:  "bg-[#1E1E28] text-[#F0EEE9] border border-[#2A2A32]",
    gold:     "bg-[#D4A853]/10 text-[#D4A853] border border-[#D4A853]/25",
    violet:   "bg-[#7C6AF7]/10 text-[#7C6AF7] border border-[#7C6AF7]/25",
    success:  "bg-[#3ECFA4]/10 text-[#3ECFA4] border border-[#3ECFA4]/25",
    warning:  "bg-[#E8A44C]/10 text-[#E8A44C] border border-[#E8A44C]/25",
    danger:   "bg-[#F06B6B]/10 text-[#F06B6B] border border-[#F06B6B]/25",
    muted:    "bg-[#1E1E28] text-[#7A7A8A] border border-[#2A2A32]",
  };
  return <span className={clsx(base, variants[variant], className)}>{children}</span>;
}
