"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, NativeSelect, Spinner } from "@/components/common";
import { api, errorMessage } from "@/lib/api";
import { fullName } from "@/lib/format";
import type { OppDetail, Stage } from "@/lib/types";

const EMPTY = { first_name: "", last_name: "", company_name: "", email: "", phone: "", stage_id: "", value: "" };

export function NewOpportunityDialog({ open, onOpenChange, stages, onCreated }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  stages: Stage[];
  onCreated: (id: string) => void;
}) {
  const qc = useQueryClient();
  const [f, setF] = React.useState(EMPTY);
  const stageId = f.stage_id || stages[0]?.id || "";
  const create = useMutation({
    mutationFn: () =>
      api<OppDetail & { reused_prospect: boolean }>("/api/opportunities", {
        body: {
          email: f.email.trim(),
          first_name: f.first_name.trim() || null,
          last_name: f.last_name.trim() || null,
          company_name: f.company_name.trim() || null,
          phone: f.phone.trim() || null,
          stage_id: stageId || null,
          value_cents: f.value ? Math.round(Number(f.value) * 100) : null,
        },
      }),
    onSuccess: (d) => {
      toast.success(`${fullName(d)} added to Opportunities`, {
        description: d.reused_prospect ? "Already a prospect, so their existing record was used (no duplicate)." : undefined,
      });
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: ["opportunity-stages"] });
      setF(EMPTY);
      onOpenChange(false);
      onCreated(d.id);
    },
    onError: (e) => toast.error("Couldn’t add the opportunity", { description: errorMessage(e) }),
  });
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New opportunity</DialogTitle>
          <DialogDescription>If this email already belongs to a prospect, that record is used instead of creating a duplicate.</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (f.email.trim()) create.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="First name" htmlFor="no-first"><Input id="no-first" autoFocus value={f.first_name} onChange={set("first_name")} autoComplete="off" /></Field>
            <Field label="Last name" htmlFor="no-last"><Input id="no-last" value={f.last_name} onChange={set("last_name")} autoComplete="off" /></Field>
          </div>
          <Field label="Business name" htmlFor="no-company"><Input id="no-company" value={f.company_name} onChange={set("company_name")} autoComplete="off" /></Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Email" htmlFor="no-email"><Input id="no-email" type="email" required value={f.email} onChange={set("email")} autoComplete="off" /></Field>
            <Field label="Phone" htmlFor="no-phone"><Input id="no-phone" type="tel" value={f.phone} onChange={set("phone")} autoComplete="off" /></Field>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Stage" htmlFor="no-stage">
              <NativeSelect id="no-stage" className="w-full" value={stageId} onChange={set("stage_id")}>
                {stages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </NativeSelect>
            </Field>
            <Field label="Deal value (USD, optional)" htmlFor="no-value">
              <Input id="no-value" type="number" min={0} step="1" inputMode="decimal" value={f.value} onChange={set("value")} />
            </Field>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={create.isPending || !f.email.trim()}>
              {create.isPending && <Spinner className="text-primary-foreground" />} Add opportunity
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
