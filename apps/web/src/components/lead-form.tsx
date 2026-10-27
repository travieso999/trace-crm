"use client";

import { useForm } from "react-hook-form";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field, NativeSelect } from "@/components/common";
import { zodResolver } from "@/lib/zod-resolver";
import { COMMON_TIMEZONES, humanize } from "@/lib/format";
import { LEAD_STATUSES, type Contact } from "@/lib/types";

const optional = (max: number) => z.string().max(max).optional();

const schema = z.object({
  email: z.email("Enter a valid email"),
  first_name: optional(120),
  last_name: optional(120),
  company_name: optional(300),
  job_title: optional(200),
  website: optional(500),
  phone: optional(60),
  city: optional(120),
  state: optional(60),
  timezone: optional(64),
  industry: optional(120),
  linkedin_url: optional(500),
  lead_status: z.enum(LEAD_STATUSES),
  verification_status: z.enum(["unknown", "valid", "risky", "invalid"]),
  tags: z.string().optional(),
  notes: optional(20000),
});
export type LeadValues = z.infer<typeof schema>;

const TEXT_FIELDS: [keyof LeadValues, string][] = [
  ["first_name", "First name"],
  ["last_name", "Last name"],
  ["company_name", "Company"],
  ["job_title", "Job title"],
  ["website", "Website"],
  ["phone", "Phone"],
  ["city", "City"],
  ["state", "State"],
  ["industry", "Industry"],
  ["linkedin_url", "LinkedIn URL"],
];

/** Convert form values to the API payload: blank strings become null, tags become a list. */
export function toPayload(v: LeadValues) {
  const out: Record<string, unknown> = {};
  for (const [k, val] of Object.entries(v)) {
    if (k === "tags") continue;
    out[k] = typeof val === "string" && val.trim() === "" ? null : val;
  }
  out.tags = (v.tags ?? "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  return out;
}

export function LeadForm({
  contact,
  submitLabel,
  onSubmit,
  onCancel,
  disabled,
}: {
  contact?: Contact;
  submitLabel: string;
  onSubmit: (values: LeadValues) => Promise<void>;
  onCancel?: () => void;
  disabled?: boolean;
}) {
  const { register, handleSubmit, formState } = useForm<LeadValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      email: contact?.email ?? "",
      first_name: contact?.first_name ?? "",
      last_name: contact?.last_name ?? "",
      company_name: contact?.company_name ?? "",
      job_title: contact?.job_title ?? "",
      website: contact?.website ?? "",
      phone: contact?.phone ?? "",
      city: contact?.city ?? "",
      state: contact?.state ?? "",
      timezone: contact?.timezone ?? "",
      industry: contact?.industry ?? "",
      linkedin_url: contact?.linkedin_url ?? "",
      lead_status: contact?.lead_status ?? "new_lead",
      verification_status: contact?.verification_status ?? "unknown",
      tags: contact?.tags.map((t) => t.name).join(", ") ?? "",
      notes: contact?.notes ?? "",
    },
  });
  const err = formState.errors;
  const timezones = contact?.timezone && !COMMON_TIMEZONES.includes(contact.timezone) ? [contact.timezone, ...COMMON_TIMEZONES] : COMMON_TIMEZONES;

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="grid gap-4">
      <fieldset disabled={disabled || formState.isSubmitting} className="grid gap-4 sm:grid-cols-2">
        <Field label="Email" htmlFor="email" error={err.email?.message} className="sm:col-span-2">
          <Input id="email" type="email" autoComplete="off" {...register("email")} aria-invalid={!!err.email} />
        </Field>
        {TEXT_FIELDS.map(([name, label]) => (
          <Field key={name} label={label} htmlFor={name} error={err[name]?.message}>
            <Input id={name} {...register(name)} aria-invalid={!!err[name]} />
          </Field>
        ))}
        <Field label="Time zone" htmlFor="timezone" hint="Leave blank to infer from state">
          <NativeSelect id="timezone" {...register("timezone")}>
            <option value="">Auto</option>
            {timezones.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Tags" htmlFor="tags" hint="Comma separated">
          <Input id="tags" {...register("tags")} />
        </Field>
        <Field label="Lead status" htmlFor="lead_status">
          <NativeSelect id="lead_status" {...register("lead_status")}>
            {LEAD_STATUSES.map((s) => (
              <option key={s} value={s}>
                {humanize(s)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Verification" htmlFor="verification_status">
          <NativeSelect id="verification_status" {...register("verification_status")}>
            {["unknown", "valid", "risky", "invalid"].map((s) => (
              <option key={s} value={s}>
                {humanize(s)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Notes" htmlFor="notes" className="sm:col-span-2">
          <Textarea id="notes" rows={3} {...register("notes")} />
        </Field>
      </fieldset>
      {!disabled && (
        <div className="flex justify-end gap-2">
          {onCancel && (
            <Button type="button" variant="outline" onClick={onCancel}>
              Cancel
            </Button>
          )}
          <Button type="submit" disabled={formState.isSubmitting}>
            {formState.isSubmitting && <Loader2 className="animate-spin" />}
            {submitLabel}
          </Button>
        </div>
      )}
    </form>
  );
}
