import type { Metadata } from "next";
import Link from "next/link";
import { GraduationCap, MonitorCheck, ShieldCheck } from "lucide-react";
import "./globals.css";

export const metadata: Metadata = {
  title: "TWELVE Pilot",
  description: "Browser-based AI viva pilot platform"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand" aria-label="TWELVE home">
            <GraduationCap size={24} />
            <span>TWELVE</span>
          </Link>
          <nav aria-label="Primary">
            <Link href="/admin">
              <MonitorCheck size={18} />
              Admin
            </Link>
            <Link href="/student">
              <ShieldCheck size={18} />
              Student Viva
            </Link>
            <Link href="/review">Review</Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
