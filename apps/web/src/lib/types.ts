export type Page<T> = { items: T[]; total: number; page: number; page_size: number };

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "member" | "viewer";
  is_active: boolean;
  last_login_at: string | null;
};

export type Tag = { id: string; name: string; color: string | null };

export const LEAD_STATUSES = [
  "new_lead",
  "contacted",
  "replied",
  "interested",
  "meeting_scheduled",
  "proposal_sent",
  "won",
  "lost",
  "not_interested",
] as const;
export type LeadStatus = (typeof LEAD_STATUSES)[number];

export type Contact = {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  company_id: string | null;
  company_name: string | null;
  website: string | null;
  phone: string | null;
  city: string | null;
  state: string | null;
  timezone: string | null;
  industry: string | null;
  job_title: string | null;
  linkedin_url: string | null;
  notes: string | null;
  source: string | null;
  verification_status: "unknown" | "valid" | "risky" | "invalid";
  lead_status: LeadStatus;
  custom_fields: Record<string, string>;
  tags: Tag[];
  last_contacted_at: string | null;
  last_replied_at: string | null;
  created_at: string;
  updated_at: string;
  suppressed: boolean;
  campaigns: { id: string; name: string; status: string }[];
};

export type Activity = {
  id: string;
  type: string;
  summary: string;
  data: Record<string, unknown>;
  created_at: string;
  user_id: string | null;
};

export type Company = {
  id: string;
  name: string;
  website: string | null;
  domain: string | null;
  industry: string | null;
  city: string | null;
  state: string | null;
  timezone: string | null;
  website_status: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
  contact_count: number;
  latest_audit: { id: string; status: string; summary: string | null; completed_at: string | null } | null;
};

export type Schedule = {
  days: number[];
  start: string;
  end: string;
  timezone: string;
  use_recipient_timezone?: boolean | null;
};

export type EmailAccount = {
  id: string;
  provider: "gmail" | "microsoft" | "sandbox";
  email: string;
  display_name: string | null;
  status: "connected" | "paused" | "auth_error" | "error" | "disconnected";
  daily_limit: number;
  min_interval_seconds: number;
  schedule: Schedule;
  signature_html: string | null;
  last_synced_at: string | null;
  last_sent_at: string | null;
  consecutive_failures: number;
  circuit_open_until: string | null;
  last_error: string | null;
  last_error_at: string | null;
  token_expires_at: string | null;
  scopes: string | null;
  created_at: string;
  sent_today: number;
  sent_total: number;
  campaigns: { id: string; name: string }[];
};

export type Step = {
  id?: string;
  position?: number;
  delay_days: number;
  business_days: boolean;
  subject: string;
  body: string;
  reply_in_thread: boolean;
};

export type CampaignStats = {
  leads: number;
  active: number;
  replied: number;
  completed: number;
  bounced: number;
  unsubscribed: number;
  sent: number;
  queued: number;
  positive: number;
  reply_rate: number;
};

export type Campaign = {
  id: string;
  name: string;
  description: string | null;
  status: "draft" | "scheduled" | "active" | "paused" | "completed" | "stopped" | "error";
  daily_limit: number;
  schedule: Schedule;
  missing_variable_policy: "block" | "fallback";
  variable_fallbacks: Record<string, string>;
  require_approved_personalization: boolean;
  stop_on_auto_reply: boolean;
  start_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  archived_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  steps: Step[];
  mailbox_ids: string[];
  stats: CampaignStats;
};

export type Template = { id: string; name: string; subject: string; body: string; created_at: string };

export type Dashboard = {
  timezone: string;
  kpis: {
    total_contacts: number;
    active_campaigns: number;
    sent_today: number;
    sent_this_month: number;
    replies_total: number;
    positive_replies_total: number;
    meetings_booked: number;
    bounce_rate_30d: number;
    unsubscribe_rate_30d: number;
    reply_rate_30d: number;
    queued_messages: number;
  };
  daily_volume: { date: string; accepted: number; failed: number; bounced: number }[];
  reply_trend: { date: string; replied: number; positive_reply: number; negative_reply: number }[];
  campaign_performance: {
    campaign_id: string;
    name: string;
    accepted: number;
    replied: number;
    reply_rate: number;
    bounce_rate: number;
  }[];
  funnel: { stage: LeadStatus; count: number }[];
  mailbox_health: {
    id: string;
    email: string;
    provider: string;
    status: string;
    sent_today: number;
    daily_limit: number;
    last_error: string | null;
    circuit_open: boolean;
    last_synced_at: string | null;
  }[];
  upcoming: { at: string; email: string; name: string; campaign: string; step: number }[];
  recent_activity: {
    id: string;
    type: string;
    summary: string;
    created_at: string;
    contact_id: string;
    contact: string;
  }[];
};
