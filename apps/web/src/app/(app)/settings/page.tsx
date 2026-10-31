"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, CircleAlert, CircleDashed } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, Field, PageHeader } from "@/components/common";
import { ScheduleEditor } from "@/components/schedule-editor";
import { useIsAdmin } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import type { SettingsResponse } from "@/lib/types";

type Sections = SettingsResponse["sections"];

function Section<K extends keyof Sections & string>({
  name,
  title,
  description,
  value,
  readOnly,
  children,
}: {
  name: K;
  title: string;
  description?: string;
  value: Sections[K];
  readOnly: boolean;
  children: (draft: Sections[K], set: (patch: Partial<Sections[K]>) => void) => React.ReactNode;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = React.useState(value);
  const [source, setSource] = React.useState(value);
  if (source !== value) {
    setSource(value);
    setDraft(value);
  }
  const dirty = JSON.stringify(draft) !== JSON.stringify(value);
  const save = useMutation({
    mutationFn: () => api(`/api/settings/${name}`, { method: "PUT", body: draft }),
    onSuccess: () => {
      toast.success(`${title} saved`);
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["campaign-preflight"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <fieldset className="grid gap-3" disabled={readOnly}>
            {children(draft, (patch) => setDraft({ ...draft, ...patch }))}
          </fieldset>
          {!readOnly && (
            <div className="flex justify-end gap-2">
              {dirty && (
                <Button type="button" variant="outline" onClick={() => setDraft(value)}>
                  Discard
                </Button>
              )}
              <Button type="submit" disabled={!dirty || save.isPending}>
                Save
              </Button>
            </div>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

function Check({ label, value }: { label: string; value: string | boolean }) {
  const icon =
    value === true ? (
      <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" aria-label="Configured" />
    ) : value === false ? (
      <CircleDashed className="size-4 text-muted-foreground" aria-label="Not configured" />
    ) : null;
  return (
    <li className="flex items-center justify-between gap-3 py-1.5 text-sm">
      <span>{label}</span>
      <span className="flex items-center gap-1.5 text-muted-foreground">
        {typeof value === "string" ? value : value ? "Configured" : "Not configured"}
        {icon}
      </span>
    </li>
  );
}

const checkbox = "size-4 accent-[var(--primary)]";

export default function SettingsPage() {
  const isAdmin = useIsAdmin();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsResponse>("/api/settings") });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isPending)
    return (
      <>
        <PageHeader title="Settings" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-56" />
          <Skeleton className="h-56" />
        </div>
      </>
    );
  const { sections: s, integrations: i, compliance_problems: problems } = q.data;
  const ro = !isAdmin;

  return (
    <>
      <PageHeader title="Settings" description={ro ? "Only admins can change settings." : "Workspace-wide defaults and compliance."} />
      {problems.length > 0 && (
        <div className="mb-4 rounded-xl border border-signal/30 bg-primary/5 p-3 text-sm" role="status">
          <p className="flex items-center gap-2 font-medium">
            <CircleAlert className="size-4 text-signal" aria-hidden /> Campaigns can’t send until this is fixed
          </p>
          <ul className="mt-1 list-disc pl-8 text-muted-foreground">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Section name="compliance" title="Compliance" description="Required by CAN-SPAM: a real postal address and a working unsubscribe link in every email." value={s.compliance} readOnly={ro}>
          {(d, set) => (
            <>
              <Field label="Physical postal address" htmlFor="c-addr">
                <Textarea id="c-addr" rows={2} value={d.physical_address} onChange={(e) => set({ physical_address: e.target.value })} />
              </Field>
              <Field label="Email footer" htmlFor="c-footer" hint="Must include {{physical_address}} and {{unsubscribe_url}}. {{company_name_sender}} is your company name.">
                <Textarea id="c-footer" rows={3} className="font-mono text-[13px]" value={d.footer_template} onChange={(e) => set({ footer_template: e.target.value })} />
              </Field>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className={checkbox} checked={d.suppress_on_unsubscribe_reply} onChange={(e) => set({ suppress_on_unsubscribe_reply: e.target.checked })} />
                Suppress people who reply asking to be removed
              </label>
            </>
          )}
        </Section>
        <Section name="company" title="Company" value={s.company} readOnly={ro}>
          {(d, set) => (
            <>
              <Field label="Company name" htmlFor="co-name">
                <Input id="co-name" value={d.name} onChange={(e) => set({ name: e.target.value })} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Website" htmlFor="co-web">
                  <Input id="co-web" value={d.website} onChange={(e) => set({ website: e.target.value })} />
                </Field>
                <Field label="Phone" htmlFor="co-phone">
                  <Input id="co-phone" value={d.phone} onChange={(e) => set({ phone: e.target.value })} />
                </Field>
              </div>
            </>
          )}
        </Section>
        <Section name="defaults" title="Sending defaults" description="Used for new mailboxes and campaigns. Existing ones keep their own settings." value={s.defaults} readOnly={ro}>
          {(d, set) => (
            <>
              <div className="grid grid-cols-3 gap-3">
                <Field label="Per mailbox / day" htmlFor="d-mb">
                  <Input id="d-mb" type="number" min={1} max={500} value={d.daily_limit_per_mailbox} onChange={(e) => set({ daily_limit_per_mailbox: Number(e.target.value) })} />
                </Field>
                <Field label="Per campaign / day" htmlFor="d-camp">
                  <Input id="d-camp" type="number" min={1} max={1000} value={d.campaign_daily_limit} onChange={(e) => set({ campaign_daily_limit: Number(e.target.value) })} />
                </Field>
                <Field label="Seconds between" htmlFor="d-gap">
                  <Input id="d-gap" type="number" min={30} max={3600} value={d.min_interval_seconds} onChange={(e) => set({ min_interval_seconds: Number(e.target.value) })} />
                </Field>
              </div>
              <ScheduleEditor value={d.schedule} disabled={ro} onChange={(schedule) => set({ schedule, timezone: schedule.timezone })} />
            </>
          )}
        </Section>
        <Section name="sending_identity" title="Sending identity" value={s.sending_identity} readOnly={ro}>
          {(d, set) => (
            <>
              <Field label="Default sender name" htmlFor="si-name">
                <Input id="si-name" value={d.default_from_name} onChange={(e) => set({ default_from_name: e.target.value })} />
              </Field>
              <Field label="Reply-To address (optional)" htmlFor="si-reply" hint="Leave empty so replies go to the sending mailbox (recommended: replies are only synced from connected mailboxes).">
                <Input id="si-reply" type="email" value={d.reply_to} onChange={(e) => set({ reply_to: e.target.value })} />
              </Field>
            </>
          )}
        </Section>
        <Section name="video_attachments" title="Video attachments" description="Your limit can be lowered freely. It never goes above what the sending provider supports: about 17.7 MB for a file, because encoding grows it to fit the 25 MB message limit." value={s.video_attachments} readOnly={ro}>
          {(d, set) => (
            <div className="grid grid-cols-3 gap-3">
              <Field label="Largest file (MB)" htmlFor="v-max">
                <Input id="v-max" type="number" min={1} step="any" value={d.max_attachment_mb} onChange={(e) => set({ max_attachment_mb: Number(e.target.value) })} />
              </Field>
              <Field label="Warn above (MB)" htmlFor="v-warn">
                <Input id="v-warn" type="number" min={1} step="any" value={d.warn_attachment_mb} onChange={(e) => set({ warn_attachment_mb: Number(e.target.value) })} />
              </Field>
              <Field label="Library total (MB)" htmlFor="v-lib">
                <Input id="v-lib" type="number" min={1} step="any" value={d.library_max_mb} onChange={(e) => set({ library_max_mb: Number(e.target.value) })} />
              </Field>
            </div>
          )}
        </Section>
        <Card>
          <CardHeader>
            <CardTitle>Integrations</CardTitle>
            <CardDescription>Configured on the server through environment variables.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              <Check label="Google (Gmail) OAuth" value={i.google_oauth_configured} />
              <Check label="Microsoft 365 OAuth" value={i.microsoft_oauth_configured} />
              <Check label="OpenAI" value={i.openai_configured} />
              <Check label="Email verification" value={i.verification_provider === "neverbounce" ? (i.neverbounce_configured ? "NeverBounce" : "NeverBounce (no key)") : "Local (syntax + MX)"} />
              <Check label="Redis (background queue)" value={i.redis_configured} />
              <Check label="Sandbox mailboxes" value={i.sandbox_enabled ? "Enabled (development)" : "Disabled"} />
              <Check label="Environment" value={i.app_env} />
              <Check label="Public URL (unsubscribe links)" value={i.public_url} />
            </ul>
          </CardContent>
        </Card>
        <Section name="notifications" title="Notifications" description="Saved now; email notifications are sent once a notification mailbox is wired up." value={s.notifications} readOnly={ro}>
          {(d, set) => (
            <>
              <Field label="Notification email" htmlFor="n-email">
                <Input id="n-email" type="email" value={d.notification_email} onChange={(e) => set({ notification_email: e.target.value })} />
              </Field>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className={checkbox} checked={d.notify_on_reply} onChange={(e) => set({ notify_on_reply: e.target.checked })} />
                New replies
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className={checkbox} checked={d.notify_on_mailbox_error} onChange={(e) => set({ notify_on_mailbox_error: e.target.checked })} />
                Mailbox errors
              </label>
            </>
          )}
        </Section>
      </div>
    </>
  );
}
