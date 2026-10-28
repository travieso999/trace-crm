"use client";

/** New campaign in two steps: details, then the audience (imported list, smart filters or a new spreadsheet). */

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, NativeSelect } from "@/components/common";
import { AudiencePicker } from "@/components/campaign/audience-picker";
import { api, errorMessage } from "@/lib/api";
import type { Campaign } from "@/lib/types";

const TYPES = ["Cold outreach", "Follow-up / re-engagement", "Event or announcement", "Partnership", "Other"];

export function NewCampaignDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [details, setDetails] = React.useState({ name: "", description: "", campaign_type: TYPES[0], target_location: "", industry: "" });
  const [created, setCreated] = React.useState<Campaign | null>(null);
  const [saving, setSaving] = React.useState(false);

  function close(o: boolean) {
    if (!o) {
      setCreated(null);
      setDetails({ name: "", description: "", campaign_type: TYPES[0], target_location: "", industry: "" });
    }
    onOpenChange(o);
  }
  function finish() {
    const id = created!.id;
    close(false);
    qc.invalidateQueries({ queryKey: ["campaigns"] });
    router.push(`/campaigns/${id}?tab=sequence`);
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const c = await api<Campaign>("/api/campaigns", {
        body: {
          name: details.name.trim(),
          description: details.description.trim() || null,
          campaign_type: details.campaign_type || null,
          target_location: details.target_location.trim() || null,
          industry: details.industry.trim() || null,
        },
      });
      setCreated(c);
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{created ? `Who should “${created.name}” go to?` : "New campaign"}</DialogTitle>
          <DialogDescription>
            {created
              ? "Add a whole group at once. You can add more later from the campaign’s Contacts tab."
              : "Step 1 of 2: describe the campaign. Next you choose its contacts, then write the emails."}
          </DialogDescription>
        </DialogHeader>
        {!created ? (
          <form onSubmit={create} className="grid gap-4">
            <Field label="Campaign name" htmlFor="nc-name" hint="Only your team sees this.">
              <Input id="nc-name" autoFocus maxLength={200} value={details.name} onChange={(e) => setDetails({ ...details, name: e.target.value })} placeholder="Texas Bail Bonds" />
            </Field>
            <Field label="Description" htmlFor="nc-desc">
              <Textarea id="nc-desc" rows={2} maxLength={5000} value={details.description} onChange={(e) => setDetails({ ...details, description: e.target.value })} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Campaign type" htmlFor="nc-type">
                <NativeSelect id="nc-type" className="w-full" value={details.campaign_type} onChange={(e) => setDetails({ ...details, campaign_type: e.target.value })}>
                  {TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Target state / location" htmlFor="nc-loc">
                <Input id="nc-loc" maxLength={120} value={details.target_location} onChange={(e) => setDetails({ ...details, target_location: e.target.value })} placeholder="Texas" />
              </Field>
              <Field label="Industry" htmlFor="nc-ind">
                <Input id="nc-ind" maxLength={120} value={details.industry} onChange={(e) => setDetails({ ...details, industry: e.target.value })} placeholder="Bail Bonds" />
              </Field>
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => close(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={saving || !details.name.trim()}>
                Next: choose contacts
              </Button>
            </div>
          </form>
        ) : (
          <div className="grid gap-4">
            <AudiencePicker campaign={created} onDone={finish} />
            <div className="flex justify-end border-t pt-3">
              <Button variant="ghost" onClick={finish}>
                Skip for now — write the emails
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
