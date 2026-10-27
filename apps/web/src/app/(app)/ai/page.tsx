"use client";

import { Sparkles } from "lucide-react";
import { NotAvailable } from "@/components/not-available";

export default function AiPage() {
  return (
    <NotAvailable
      title="AI Personalization"
      icon={Sparkles}
      summary="Generated opening lines with evidence and human approval are planned for Phase F. Approved lines will fill {{custom_intro}} in your sequences."
      needs={["Generation + approval API (Phase F)", "An approved OpenAI key and daily token budget"]}
      integration={{ label: "OpenAI", key: "openai_configured" }}
    />
  );
}
