"use client";

import type { LucideIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/common";
import { api } from "@/lib/api";
import type { SettingsResponse } from "@/lib/types";

/** Honest placeholder for a module whose backend isn't built yet, with the real integration status it depends on. */
export function NotAvailable({
  title,
  icon: Icon,
  summary,
  needs,
  integration,
}: {
  title: string;
  icon: LucideIcon;
  summary: string;
  needs: string[];
  integration?: { label: string; key: keyof SettingsResponse["integrations"] };
}) {
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsResponse>("/api/settings"), enabled: !!integration });
  const configured = integration ? settings.data?.integrations[integration.key] : undefined;
  return (
    <>
      <PageHeader title={title} />
      <div className="flex flex-col items-center rounded-xl border border-dashed px-6 py-14 text-center">
        <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
          <Icon className="size-5 text-muted-foreground" aria-hidden />
        </div>
        <h2 className="text-sm font-medium">Not available yet</h2>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{summary}</p>
        <ul className="mt-4 grid gap-1 text-left text-sm text-muted-foreground">
          {needs.map((n) => (
            <li key={n}>• {n}</li>
          ))}
          {integration && (
            <li>
              • {integration.label}:{" "}
              <span className="font-medium text-foreground">{configured === undefined ? "checking…" : configured ? "configured" : "not configured"}</span>
            </li>
          )}
        </ul>
      </div>
    </>
  );
}
