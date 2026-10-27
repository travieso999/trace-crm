"use client";

import { useForm } from "react-hook-form";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field } from "@/components/common";
import { zodResolver } from "@/lib/zod-resolver";
import type { Company } from "@/lib/types";

const schema = z.object({
  name: z.string().trim().min(1, "Name is required").max(300),
  website: z.string().max(500).optional(),
  industry: z.string().max(120).optional(),
  city: z.string().max(120).optional(),
  state: z.string().max(60).optional(),
  notes: z.string().max(20000).optional(),
});
export type CompanyValues = z.infer<typeof schema>;

export function companyPayload(v: CompanyValues) {
  return Object.fromEntries(Object.entries(v).map(([k, val]) => [k, typeof val === "string" && val.trim() === "" ? null : val]));
}

export function CompanyForm({
  company,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  company?: Company;
  submitLabel: string;
  onSubmit: (v: CompanyValues) => Promise<void>;
  onCancel?: () => void;
}) {
  const { register, handleSubmit, formState } = useForm<CompanyValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: company?.name ?? "",
      website: company?.website ?? "",
      industry: company?.industry ?? "",
      city: company?.city ?? "",
      state: company?.state ?? "",
      notes: company?.notes ?? "",
    },
  });
  const err = formState.errors;
  return (
    <form onSubmit={handleSubmit(onSubmit)} className="grid gap-4">
      <fieldset disabled={formState.isSubmitting} className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" htmlFor="c-name" error={err.name?.message} className="sm:col-span-2">
          <Input id="c-name" {...register("name")} aria-invalid={!!err.name} />
        </Field>
        <Field label="Website" htmlFor="c-website" error={err.website?.message}>
          <Input id="c-website" placeholder="example.com" {...register("website")} />
        </Field>
        <Field label="Industry" htmlFor="c-industry">
          <Input id="c-industry" {...register("industry")} />
        </Field>
        <Field label="City" htmlFor="c-city">
          <Input id="c-city" {...register("city")} />
        </Field>
        <Field label="State" htmlFor="c-state">
          <Input id="c-state" {...register("state")} />
        </Field>
        <Field label="Notes" htmlFor="c-notes" className="sm:col-span-2">
          <Textarea id="c-notes" rows={3} {...register("notes")} />
        </Field>
      </fieldset>
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
    </form>
  );
}
