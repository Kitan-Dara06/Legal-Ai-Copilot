import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lex — Legal AI Copilot",
  description: "AI-powered legal analysis, drafting, and review for modern law practice.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="antialiased min-h-screen" style={{ background: "var(--color-bg, #0E0E12)", color: "var(--color-text-primary, #F0EEE9)" }}>
        {children}
        <Analytics />
      </body>
    </html>
  );
}
