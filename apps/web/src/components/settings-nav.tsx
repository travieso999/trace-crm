"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "cn";
import { useIsAdmin } from "@/components/app-shell";

const TABS = [
  { href: "/settings", label: "General" },
  { href: "/settings/team", label: "Team Management", adminOnly: true },
];

/** Tabs across the Settings pages. Team Management is only listed for administrators. */
export function SettingsNav() {
  const pathname = usePathname();
  const isAdmin = useIsAdmin();
  return (
    <nav aria-label="Settings sections" className="mb-5 flex gap-5 border-b">
      {TABS.filter((t) => !t.adminOnly || isAdmin).map((t) => {
        const active = pathname === t.href;
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 border-transparent pb-2.5 text-sm text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:text-foreground",
              active && "border-primary font-medium text-foreground",
            )}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
