"use client";
import { clsx } from "clsx";

interface CardProps {
  children: React.ReactNode;
  className?: string;
  gold?: boolean;   // amber glow variant
  violet?: boolean; // violet glow variant
}

export function Card({ children, className, gold, violet }: CardProps) {
  return (
    <div
      className={clsx(
        "rounded-xl border bg-[#16161D] border-[#2A2A32] transition-colors",
        gold && "border-[#D4A853]/20 bg-[#D4A853]/[0.04]",
        violet && "border-[#7C6AF7]/20 bg-[#7C6AF7]/[0.04]",
        className,
      )}
    >
      {children}
    </div>
  );
}

interface CardHeaderProps {
  children: React.ReactNode;
  className?: string;
}
export function CardHeader({ children, className }: CardHeaderProps) {
  return (
    <div className={clsx("px-5 pt-5 pb-4 border-b border-[#2A2A32]", className)}>
      {children}
    </div>
  );
}

interface CardBodyProps {
  children: React.ReactNode;
  className?: string;
}
export function CardBody({ children, className }: CardBodyProps) {
  return <div className={clsx("p-5", className)}>{children}</div>;
}

// Legacy named exports for old pages
export function CardContent({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={className ?? "p-5"}>{children}</div>;
}
export function CardTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return <h3 className={`text-base font-semibold text-[#F0EEE9] ${className ?? ""}`}>{children}</h3>;
}
export function CardDescription({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={`text-sm text-[#7A7A8A] ${className ?? ""}`}>{children}</p>;
}
