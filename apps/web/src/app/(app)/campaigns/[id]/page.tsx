"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Archive, ArchiveRestore, ArrowLeft, CheckCircle2, CircleAlert, Copy, Download, FileSpreadsheet, Filter, MailPlus, MoreHorizontal, Pause, Play, Rocket, Square, Trash2, Upload, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, ErrorState, StatusBadge } from "@/components/common";
import { SequenceEditor } from "@/components/campaign/sequence-editor";
import { CampaignSettings } from "@/components/campaign/campaign-tabs";
import { CampaignContacts } from "@/components/campaign/campaign-contacts";
import { CampaignAnalyticsTab, CampaignHistory } from "@/components/campaign/campaign-history";
import { AddContactsDialog, type AudienceTab } from "@/components/campaign/audience-picker";
import { SendConfirmDialog } from "@/components/campaign/send-confirm-dialog";
import { stepLabel } from "@/components/campaign/labels";
import { useCanWrite, useIsAdmin } from "@/components/app-shell";
import { ApiError, api, download, errorMessage } from "@/lib/api";
import { dateTime, num, pct } from "@/lib/format";
import type { Campaign, Preflight } from "@/lib/types";

const TABS = ["overview", "contacts", "sequence", "history", "analytics", "settings"] as const;
// old links (?tab=leads / ?tab=messages) open the tabs that replaced them
const ALIASES: Record<string, (typeof TABS)[number]> = { leads: "contacts", messages: "history" };
type Tab = (typeof TABS)[number];

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
        {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function Overview({ campaign, onTab }: { campaign: Campaign; onTab: (t: Tab) => void }) {
  const s = campaign.stats;
  const details: [string, React.ReactNode][] = [
    ["Type", campaign.campaign_type],
    ["Target location", campaign.target_location],
    ["Industry", campaign.industry],
    [
      "Imported lists",
      campaign.import_lists.length ? (
        <span className="flex flex-wrap gap-1">
          {campaign.import_lists.map((l) => (
            <Link key={l.id} href={`/prospects?batch_id=${l.id}`} className="rounded-md bg-muted px-1.5 py-0.5 text-xs hover:underline">
              {l.name}
            </Link>
          ))}
        </span>
      ) : null,
    ],
    [
      "Audience",
      campaign.audience_mode === "dynamic"
        ? "Dynamic: new prospects matching the saved filters are added automatically"
        : "Fixed: only prospects you add",
    ],
    [
      "Emails",
      campaign.steps.map((st, i) => `${stepLabel(i + 1, st.name)}${st.status && st.status !== "active" ? ` (${st.status})` : ""}`).join(" → "),
    ],
  ];
  const pre = useQuery({
    queryKey: ["campaign-preflight", campaign.id],
    queryFn: () => api<Preflight>(`/api/campaigns/${campaign.id}/preflight`),
    enabled: !["completed", "stopped"].includes(campaign.status),
  });
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Contacts" value={num(s.leads)} sub={`${num(s.active)} still in the sequence`} />
        <Stat label="Emails sent" value={num(s.sent)} sub={`${num(s.queued)} queued`} />
        <Stat label="Replies" value={num(s.replied)} sub={`${pct(s.reply_rate)} reply rate`} />
        <Stat label="Positive" value={num(s.positive)} sub={`${num(s.bounced)} bounced · ${num(s.unsubscribed)} unsubscribed`} />
      </div>
      {pre.data && (
        <Card>
          <CardHeader>
            <CardTitle>{pre.data.problems.length ? "Before you can send" : "Ready to send"}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 text-sm">
            {pre.data.problems.length === 0 ? (
              <p className="flex items-center gap-2 text-muted-foreground">
                <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" aria-hidden /> Sequence, mailboxes, prospects and compliance settings are in place.
              </p>
            ) : (
              <ul className="grid gap-1.5">
                {pre.data.problems.map((p) => (
                  <li key={p} className="flex items-start gap-2">
                    <CircleAlert className="mt-0.5 size-4 shrink-0 text-signal" aria-hidden />
                    <span>
                      {p}
                      {/Settings/.test(p) && (
                        <Link href="/settings" className="ml-1 text-signal hover:underline">
                          Open settings
                        </Link>
                      )}
                      {/step|Step|subject/.test(p) && (
                        <button type="button" className="ml-1 text-signal hover:underline" onClick={() => onTab("sequence")}>
                          Edit sequence
                        </button>
                      )}
                      {/email account/.test(p) && (
                        <button type="button" className="ml-1 text-signal hover:underline" onClick={() => onTab("settings")}>
                          Choose mailboxes
                        </button>
                      )}
                      {/Enroll/.test(p) && (
                        <button type="button" className="ml-1 text-signal hover:underline" onClick={() => onTab("contacts")}>
                          Add contacts
                        </button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {pre.data.variables.leads_blocked > 0 && (
              <p className="rounded-md bg-amber-500/12 px-2 py-1.5 text-amber-800 dark:text-amber-300" role="status">
                {num(pre.data.variables.leads_blocked)} prospect(s) are missing{" "}
                {Object.keys(pre.data.variables.by_variable).map((v) => `{{${v}}}`).join(", ")} and will not be sent to unless you fill the data or
                switch to fallback values.
              </p>
            )}
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Campaign details</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          {details.map(([label, value]) => (
            <React.Fragment key={label}>
              <span className="text-muted-foreground">{label}</span>
              <span>{value || "—"}</span>
            </React.Fragment>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Timeline</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <span className="text-muted-foreground">Created</span>
          <span>{dateTime(campaign.created_at)}</span>
          {campaign.start_at && (
            <>
              <span className="text-muted-foreground">Scheduled start</span>
              <span>{dateTime(campaign.start_at)}</span>
            </>
          )}
          <span className="text-muted-foreground">Started</span>
          <span>{dateTime(campaign.started_at)}</span>
          <span className="text-muted-foreground">Finished</span>
          <span>{dateTime(campaign.completed_at)}</span>
        </CardContent>
      </Card>
    </div>
  );
}

function CampaignDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const isAdmin = useIsAdmin();
  const raw = params.get("tab") ?? "overview";
  const initial = (ALIASES[raw] ?? raw) as Tab;
  const [tab, setTab] = React.useState<Tab>(TABS.includes(initial) ? initial : "overview");
  const [confirm, setConfirm] = React.useState<"stop" | "delete" | null>(null);
  const [adding, setAdding] = React.useState<AudienceTab | null>(null);
  const [sending, setSending] = React.useState(false);
  const [addFollowUp, setAddFollowUp] = React.useState(0);

  const q = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => api<Campaign>(`/api/campaigns/${id}`),
    refetchInterval: (query) => (query.state.data?.status === "active" ? 30_000 : false),
  });

  const action = useMutation({
    mutationFn: (name: string) => api<Campaign>(`/api/campaigns/${id}/${name}`, { method: "POST" }),
    onSuccess: (c, name) => {
      if (name === "duplicate") {
        toast.success("Campaign duplicated as a draft");
        qc.invalidateQueries({ queryKey: ["campaigns"] });
        router.push(`/campaigns/${c.id}`);
        return;
      }
      qc.setQueryData(["campaign", id], c);
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      qc.invalidateQueries({ queryKey: ["campaign-preflight", id] });
      qc.invalidateQueries({ queryKey: ["campaign-leads", id] });
      qc.invalidateQueries({ queryKey: ["steps-report", id] });
      setSending(false);
      toast.success(
        { start: c.status === "scheduled" ? "Campaign scheduled" : "Campaign started", pause: "Campaign paused", resume: "Campaign resumed", stop: "Campaign stopped", archive: "Campaign archived", unarchive: "Campaign restored" }[name] ?? "Done",
      );
      setConfirm(null);
    },
    onError: (e, name) => {
      if (e instanceof ApiError && e.status === 409 && name === "start") {
        setSending(false);
        setTab("overview");
      }
      toast.error(errorMessage(e));
    },
  });
  const del = useMutation({
    mutationFn: () => api(`/api/campaigns/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Campaign deleted");
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      router.replace("/campaigns");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isPending)
    return (
      <div className="grid gap-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-64" />
      </div>
    );
  const c = q.data;
  const st = c.status;
  const finishedStatus = st === "completed" || st === "stopped";

  return (
    <>
      <Link href="/campaigns" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Campaigns
      </Link>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">{c.name}</h1>
            <StatusBadge status={st} />
            {c.archived_at && <StatusBadge status="archived" />}
          </div>
          {c.last_error && <p className="mt-1 text-sm text-destructive">{c.last_error}</p>}
          {(c.industry || c.target_location || c.campaign_type) && (
            <p className="mt-1 text-sm text-muted-foreground">{[c.campaign_type, c.industry, c.target_location].filter(Boolean).join(" · ")}</p>
          )}
          {c.description && <p className="mt-1 text-sm text-muted-foreground">{c.description}</p>}
        </div>
        {canWrite && (
          <div className="flex flex-wrap items-center gap-2">
            {st === "draft" && (
              <Button onClick={() => setSending(true)} disabled={action.isPending}>
                <Rocket /> Send email
              </Button>
            )}
            {!finishedStatus && (
              <Button
                variant="outline"
                onClick={() => {
                  setTab("sequence");
                  setAddFollowUp((n) => n + 1);
                }}
              >
                <MailPlus /> Add follow-up
              </Button>
            )}
            {!finishedStatus && !c.archived_at && (
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="outline" />}>
                  <UserPlus /> Add contacts
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setAdding("list")}>
                    <FileSpreadsheet /> From an imported list
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setAdding("filters")}>
                    <Filter /> With smart filters
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setAdding("upload")}>
                    <Upload /> Upload a spreadsheet
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {(st === "active" || st === "scheduled") && (
              <Button variant="outline" onClick={() => action.mutate("pause")} disabled={action.isPending}>
                <Pause /> Pause
              </Button>
            )}
            {(st === "paused" || st === "error") && (
              <Button onClick={() => action.mutate("resume")} disabled={action.isPending}>
                <Play /> Resume
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="outline" size="icon" aria-label="More campaign actions" />}>
                <MoreHorizontal />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => download(`/api/campaigns/${id}/contacts/export`, undefined, `${c.name}.csv`).catch((e) => toast.error(errorMessage(e)))}
                >
                  <Download /> Export contacts
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => action.mutate("duplicate")}>
                  <Copy /> Duplicate
                </DropdownMenuItem>
                {c.archived_at ? (
                  <DropdownMenuItem onClick={() => action.mutate("unarchive")}>
                    <ArchiveRestore /> Restore from archive
                  </DropdownMenuItem>
                ) : (
                  st !== "active" && st !== "scheduled" && (
                    <DropdownMenuItem onClick={() => action.mutate("archive")}>
                      <Archive /> Archive
                    </DropdownMenuItem>
                  )
                )}
                {["active", "paused", "scheduled", "error"].includes(st) && (
                  <DropdownMenuItem onClick={() => setConfirm("stop")}>
                    <Square /> Stop for good
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={() => setConfirm("delete")}>
                  <Trash2 /> Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <TabsList variant="line">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="contacts">Contacts ({num(c.stats.leads)})</TabsTrigger>
            <TabsTrigger value="sequence">Email Sequence ({c.steps.length})</TabsTrigger>
            <TabsTrigger value="history">Email History</TabsTrigger>
            <TabsTrigger value="analytics">Analytics</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="overview" className="pt-3">
          <Overview campaign={c} onTab={setTab} />
        </TabsContent>
        <TabsContent value="contacts" className="pt-3">
          <CampaignContacts campaign={c} canWrite={canWrite} onAdd={setAdding} />
        </TabsContent>
        <TabsContent value="sequence" className="pt-3">
          <SequenceEditor campaign={c} canWrite={canWrite} addSignal={addFollowUp} />
        </TabsContent>
        <TabsContent value="history" className="pt-3">
          <CampaignHistory campaign={c} canSimulate={isAdmin} />
        </TabsContent>
        <TabsContent value="analytics" className="pt-3">
          <CampaignAnalyticsTab campaign={c} />
        </TabsContent>
        <TabsContent value="settings" className="pt-3">
          <CampaignSettings campaign={c} canWrite={canWrite} />
        </TabsContent>
      </Tabs>

      <AddContactsDialog campaign={c} tab={adding} onOpenChange={(o) => !o && setAdding(null)} />
      <SendConfirmDialog
        campaign={c}
        open={sending}
        onOpenChange={setSending}
        pending={action.isPending}
        onConfirm={() => action.mutate("start")}
        onFix={(t) => {
          setSending(false);
          setTab(t);
        }}
      />
      <ConfirmDialog
        open={confirm === "stop"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title="Stop this campaign?"
        description="Queued emails are cancelled and every active prospect is stopped. A stopped campaign can’t be restarted — duplicate it to run again."
        confirmLabel="Stop campaign"
        destructive
        pending={action.isPending}
        onConfirm={() => action.mutate("stop")}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Delete “${c.name}”?`}
        description="Sending stops immediately. Sent history and replies stay in the inbox and analytics."
        confirmLabel="Delete campaign"
        destructive
        pending={del.isPending}
        onConfirm={() => del.mutate()}
      />
    </>
  );
}

export default function CampaignPage() {
  return (
    <React.Suspense fallback={<Skeleton className="h-64" />}>
      <CampaignDetail />
    </React.Suspense>
  );
}
