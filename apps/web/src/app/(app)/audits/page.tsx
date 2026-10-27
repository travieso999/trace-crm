"use client";

import { FileSearch } from "lucide-react";
import { NotAvailable } from "@/components/not-available";

export default function AuditsPage() {
  return (
    <NotAvailable
      title="Website Audits"
      icon={FileSearch}
      summary="Automated checks of prospects’ websites (load, mobile, SSL, contact details) are planned for Phase F, using an SSRF-safe fetcher."
      needs={["Audit engine and SSRF-safe fetcher (Phase F)", "Background worker to run audits in batches"]}
    />
  );
}
