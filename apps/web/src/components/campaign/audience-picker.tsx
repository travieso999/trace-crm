"use client";

/** Add a whole group of prospects to a campaign: an imported list, smart filters, or a new spreadsheet. */

import * as React from "react";
import { cn } from "cn";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileSpreadsheet, Filter, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, NativeSelect } from "@/components/common";
import { ImportFlow } from "@/components/import-flow";
import { api, errorMessage } from "@/lib/api";
import { humanize, num } from "@/lib/format";
import { LEAD_STATUSES, type AudienceFilter, type Campaign, type EnrollResult, type ImportList, type Tag } from "@/lib/types";

export type AudienceTab = "list" | "filters" | "upload";

const TABS: { id: AudienceTab; label: string; icon: typeof Upload }[] = [
  { id: "list", label: "Imported list", icon: FileSpreadsheet },
  { id: "filters", label: "Smart filters", icon: Filter },
  { id: "upload", label: "Upload spreadsheet", icon: Upload },
];

function useDebouncedValue<T>(value: T, ms = 300) {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Strip empty values so "" never reaches the API as a condition. */
export function cleanFilter(f: AudienceFilter): AudienceFilter {
  const out: AudienceFilter = {};
  for (const [k, v] of Object.entries(f)) {
    if (k === "custom") {
      const custom = Object.fromEntries(Object.entries((v as Record<string, string>) ?? {}).filter(([ck, cv]) => ck.trim() && String(cv).trim()));
      if (Object.keys(custom).length) out.custom = custom;
    } else if (typeof v === "string" ? v.trim() : v) {
      (out as Record<string, unknown>)[k] = typeof v === "string" ? v.trim() : v;
    }
  }
  return out;
}

export function useAudienceCount(filter: AudienceFilter, enabled = true) {
  const f = useDebouncedValue(cleanFilter(filter));
  const { custom, ...rest } = f;
  const customKey = custom ? Object.keys(custom)[0] : undefined;
  return useQuery({
    queryKey: ["contacts-count", f],
    // custom fields aren't URL params; the count endpoint ignores them, so the total is an upper bound then
    queryFn: () => api<{ total: number }>("/api/contacts/count", { query: rest as Record<string, string> }),
    enabled: enabled && Object.keys(rest).length + (customKey ? 1 : 0) > 0,
  });
}

export function SmartFilterFields({ value, onChange }: { value: AudienceFilter; onChange: (f: AudienceFilter) => void }) {
  const lists = useQuery({ queryKey: ["import-lists"], queryFn: () => api<ImportList[]>("/api/imports") });
  const tags = useQuery({ queryKey: ["contact-tags"], queryFn: () => api<Tag[]>("/api/contacts/tags") });
  const set = (patch: Partial<AudienceFilter>) => onChange({ ...value, ...patch });
  const [customKey, customValue] = Object.entries(value.custom ?? {})[0] ?? ["", ""];
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="State" htmlFor="sf-state" hint="e.g. TX or TX, OK">
          <Input id="sf-state" value={value.state ?? ""} onChange={(e) => set({ state: e.target.value })} />
        </Field>
        <Field label="City" htmlFor="sf-city">
          <Input id="sf-city" value={value.city ?? ""} onChange={(e) => set({ city: e.target.value })} />
        </Field>
        <Field label="Industry" htmlFor="sf-industry" hint="Contains, e.g. bail">
          <Input id="sf-industry" value={value.industry ?? ""} onChange={(e) => set({ industry: e.target.value })} />
        </Field>
        <Field label="Imported list" htmlFor="sf-list">
          <NativeSelect id="sf-list" className="w-full" value={value.batch_id ?? ""} onChange={(e) => set({ batch_id: e.target.value })}>
            <option value="">Any list</option>
            {(lists.data ?? []).map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Tag" htmlFor="sf-tag">
          <NativeSelect id="sf-tag" className="w-full" value={value.tag ?? ""} onChange={(e) => set({ tag: e.target.value })}>
            <option value="">Any tag</option>
            {(tags.data ?? []).map((t) => (
              <option key={t.id} value={t.name}>
                {t.name}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Previous campaigns" htmlFor="sf-part">
          <NativeSelect id="sf-part" className="w-full" value={value.participation ?? ""}
            onChange={(e) => set({ participation: (e.target.value || undefined) as AudienceFilter["participation"] })}>
            <option value="">Any</option>
            <option value="never">Never in a campaign</option>
            <option value="previous">Was in a campaign before</option>
          </NativeSelect>
        </Field>
        <Field label="Prospect status" htmlFor="sf-status">
          <NativeSelect id="sf-status" className="w-full" value={value.lead_status ?? ""} onChange={(e) => set({ lead_status: e.target.value })}>
            <option value="">Any</option>
            {LEAD_STATUSES.map((s) => (
              <option key={s} value={s}>
                {humanize(s)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Email status" htmlFor="sf-verif">
          <NativeSelect id="sf-verif" className="w-full" value={value.verification_status ?? ""} onChange={(e) => set({ verification_status: e.target.value })}>
            <option value="">Any</option>
            {["valid", "risky", "unknown"].map((s) => (
              <option key={s} value={s}>
                {humanize(s)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Custom field" htmlFor="sf-ck" hint="Field name = value">
          <div className="flex gap-1">
            <Input id="sf-ck" aria-label="Custom field name" placeholder="county" value={customKey}
              onChange={(e) => set({ custom: e.target.value ? { [e.target.value]: customValue } : {} })} />
            <Input aria-label="Custom field value" placeholder="Travis" value={customValue}
              onChange={(e) => set({ custom: customKey ? { [customKey]: e.target.value } : {} })} />
          </div>
        </Field>
      </div>
    </div>
  );
}

function summarize(r: EnrollResult) {
  const skipped = r.skipped_existing + r.skipped_suppressed + r.skipped_invalid;
  return skipped
    ? `${num(r.skipped_existing)} already in the campaign · ${num(r.skipped_suppressed)} suppressed or unsubscribed · ${num(r.skipped_invalid)} invalid email or closed`
    : undefined;
}

export function AudiencePicker({ campaign, initialTab = "list", onDone }: { campaign: Campaign; initialTab?: AudienceTab; onDone: () => void }) {
  const qc = useQueryClient();
  const [tab, setTab] = React.useState<AudienceTab>(initialTab);
  const lists = useQuery({ queryKey: ["import-lists"], queryFn: () => api<ImportList[]>("/api/imports") });
  const [listId, setListId] = React.useState("");
  const list = lists.data?.find((l) => l.id === (listId || lists.data?.[0]?.id));
  const [filter, setFilter] = React.useState<AudienceFilter>(campaign.audience_filter ?? {});
  const [mode, setMode] = React.useState<Campaign["audience_mode"]>(campaign.audience_mode ?? "fixed");
  const f = cleanFilter(filter);
  const count = useAudienceCount({ ...f, not_in_campaign_id: campaign.id }, tab === "filters");
  const hasFilter = Object.keys(f).length > 0;

  function refresh() {
    for (const k of ["campaign", "campaigns", "campaign-leads", "campaign-preflight", "campaign-summary", "import-lists"]) qc.invalidateQueries({ queryKey: [k] });
  }
  const addList = useMutation({
    mutationFn: () => api<EnrollResult>(`/api/campaigns/${campaign.id}/contacts`, { body: { batch_id: list!.id } }),
    onSuccess: (r) => {
      toast.success(`${num(r.enrolled)} prospect${r.enrolled === 1 ? "" : "s"} from “${list?.name}” added`, { description: summarize(r) });
      refresh();
      onDone();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const addFilter = useMutation({
    mutationFn: async () => {
      // dynamic: remember the filters so prospects added later that match join automatically
      await api(`/api/campaigns/${campaign.id}`, { method: "PATCH", body: { audience_filter: mode === "dynamic" ? f : campaign.audience_filter, audience_mode: mode } });
      return api<EnrollResult>(`/api/campaigns/${campaign.id}/contacts`, { body: { filter: f } });
    },
    onSuccess: (r) => {
      toast.success(`${num(r.enrolled)} matching prospect${r.enrolled === 1 ? "" : "s"} added`, {
        description: mode === "dynamic" ? "New prospects matching these filters will be added automatically while the campaign runs." : summarize(r),
      });
      refresh();
      onDone();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <div className="grid gap-4">
      <div role="tablist" aria-label="How to add prospects" className="grid grid-cols-3 gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex flex-col items-center gap-1 rounded-lg border px-2 py-3 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring sm:flex-row sm:justify-center sm:text-sm",
              tab === t.id ? "border-primary bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-muted",
            )}
          >
            <t.icon className="size-4" aria-hidden /> {t.label}
          </button>
        ))}
      </div>

      {tab === "list" &&
        (lists.data && lists.data.length === 0 ? (
          <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
            No imported lists yet.{" "}
            <button type="button" className="text-signal hover:underline" onClick={() => setTab("upload")}>
              Upload a spreadsheet
            </button>{" "}
            — it becomes a list automatically.
          </p>
        ) : (
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (list) addList.mutate();
            }}
          >
            <Field label="List" htmlFor="ap-list">
              <NativeSelect id="ap-list" className="w-full" value={list?.id ?? ""} onChange={(e) => setListId(e.target.value)}>
                {(lists.data ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name} — {num(l.contacts)} contacts
                  </option>
                ))}
              </NativeSelect>
            </Field>
            {list && (
              <p className="text-sm text-muted-foreground">
                Adds all {num(list.contacts)} prospects ({num(list.valid_contacts)} can be emailed). Suppressed, unsubscribed, invalid and closed
                prospects are skipped automatically.
              </p>
            )}
            <div className="flex justify-end">
              <Button type="submit" disabled={!list || addList.isPending}>
                Add all {num(list?.contacts)} contacts
              </Button>
            </div>
          </form>
        ))}

      {tab === "filters" && (
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (hasFilter) addFilter.mutate();
          }}
        >
          <SmartFilterFields value={filter} onChange={setFilter} />
          <fieldset className="grid gap-2 rounded-lg border p-3">
            <legend className="px-1 text-sm font-medium">Audience type</legend>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" name="ap-mode" className="mt-0.5 size-4 accent-[var(--primary)]" checked={mode === "fixed"} onChange={() => setMode("fixed")} />
              <span>
                <span className="font-medium">Fixed</span> — add the prospects matching now. Later imports are not added.
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="radio" name="ap-mode" className="mt-0.5 size-4 accent-[var(--primary)]" checked={mode === "dynamic"} onChange={() => setMode("dynamic")} />
              <span>
                <span className="font-medium">Dynamic</span> — add them now, and keep adding new prospects that match while the campaign runs. They
                start at the first email. Prospects you remove are never added back.
              </span>
            </label>
          </fieldset>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm" aria-live="polite">
              {!hasFilter ? (
                <span className="text-muted-foreground">Choose at least one filter.</span>
              ) : count.data ? (
                <>
                  <span className="font-semibold tabular-nums">{num(count.data.total)}</span> matching prospect{count.data.total === 1 ? "" : "s"} not yet in
                  this campaign{f.custom ? " (before the custom field filter)" : ""}
                </>
              ) : (
                <span className="text-muted-foreground">Counting…</span>
              )}
            </p>
            <Button type="submit" disabled={!hasFilter || addFilter.isPending || (count.data?.total === 0 && mode === "fixed")}>
              {mode === "dynamic" ? "Add and keep updating" : `Add ${count.data ? num(count.data.total) : ""} prospects`}
            </Button>
          </div>
        </form>
      )}

      {tab === "upload" && <ImportFlow campaignId={campaign.id} campaignName={`“${campaign.name}”`} onImported={() => refresh()} />}
    </div>
  );
}

export function AddContactsDialog({ campaign, tab, onOpenChange }: { campaign: Campaign; tab: AudienceTab | null; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={!!tab} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Add contacts to “{campaign.name}”</DialogTitle>
          <DialogDescription>Add a whole group at once. Prospects already in the campaign are never added twice.</DialogDescription>
        </DialogHeader>
        {tab && <AudiencePicker key={tab} campaign={campaign} initialTab={tab} onDone={() => (tab === "upload" ? undefined : onOpenChange(false))} />}
      </DialogContent>
    </Dialog>
  );
}
