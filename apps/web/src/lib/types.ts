export type Page<T> = { items: T[]; total: number; page: number; page_size: number };

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "member" | "viewer";
  is_active: boolean;
  last_login_at: string | null;
  mfa_enabled: boolean;
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

type ProviderInfo = {
  available: boolean;
  configured: boolean;
  reason: string | null;
  outbox_dir?: string | null;
  redirect_uri?: string;
  scopes?: string[];
};

export type MailProviders = Record<"gmail" | "microsoft" | "sandbox", ProviderInfo> & {
  real_sending: "disabled" | "test_only" | "enabled";
};

export type Preflight = { problems: string[]; variables: { leads_blocked: number; by_variable: Record<string, number> } };

export type CampaignLead = {
  contact_id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  lead_status: LeadStatus;
  status: string;
  current_step: number;
  total_steps: number;
  next_send_at: string | null;
  stop_reason: string | null;
  mailbox: string | null;
  replied_at: string | null;
  enrolled_at: string;
};

export type CampaignMessage = {
  id: string;
  status: string;
  to: string;
  step: number;
  subject: string | null;
  due_at: string;
  sent_at: string | null;
  sent_message_id: string | null;
  mailbox: string | null;
  mailbox_id: string | null;
  mailbox_provider: EmailAccount["provider"] | null;
  attempts: number;
  last_error: string | null;
};

export type EnrollResult = { enrolled: number; skipped_existing: number; skipped_suppressed: number; skipped_invalid: number };

export type PreviewResult = {
  ok: boolean;
  subject?: string;
  html?: string;
  text?: string;
  missing: string[];
  recipient: string;
  variables_used: string[];
};

export type InboxItem = {
  id: string;
  subject: string | null;
  is_read: boolean;
  intent: string | null;
  last_message_at: string | null;
  mailbox: string | null;
  campaign: string | null;
  contact: Pick<Contact, "id" | "email" | "first_name" | "last_name" | "company_name" | "lead_status"> | null;
  from_email: string | null;
  from_name: string | null;
  kind: string | null;
  snippet: string;
};

export type InboxPage = Page<InboxItem> & { unread: number };

export type ThreadMessage = {
  id: string;
  direction: "in" | "out";
  kind: string;
  from_email: string;
  from_name: string | null;
  to: string | null;
  subject: string | null;
  html: string | null;
  text: string | null;
  intent?: string | null;
  at: string;
};

export type Thread = {
  id: string;
  subject: string | null;
  is_read: boolean;
  intent: string | null;
  mailbox: { id: string; email: string; provider: string; status: string } | null;
  contact: (Pick<Contact, "id" | "email" | "first_name" | "last_name" | "company_name" | "lead_status" | "phone">) | null;
  campaign: { id: string; name: string } | null;
  messages: ThreadMessage[];
};

export type Task = {
  id: string;
  contact_id: string;
  title: string;
  due_at: string | null;
  completed_at: string | null;
  created_at: string;
  contact?: { email: string; first_name: string | null; last_name: string | null; company_name: string | null };
};

// --- Opportunities CRM ---------------------------------------------------------------------------

export type Stage = { id: string; name: string; color: string; position: number; lead_status: LeadStatus | null; count: number };

export type OppStatus = "open" | "won" | "lost";

export type TaskBrief = { id: string; title: string; task_type: string; due_at: string | null; timezone: string | null; overdue: boolean };

export type OppCard = {
  id: string;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  email: string;
  phone: string | null;
  stage_id: string;
  stage_name: string;
  stage_color: string;
  status: OppStatus;
  value_cents: number | null;
  next_task: TaskBrief | null;
  open_tasks: number;
  updated_at: string;
};

export type BoardStage = Omit<Stage, "count"> & { count: number; value_cents: number; items: OppCard[] };

export type OppDetail = OppCard & {
  contact_id: string;
  lead_status: LeadStatus;
  on_board: boolean;
  company: { id: string; name: string; website: string | null } | null;
  website: string | null;
  city: string | null;
  state: string | null;
  timezone: string | null;
  meeting_at: string | null;
  created_at: string;
  next_task_detail: TaskBrief | null;
  latest_note: { id: string; body: string; created_at: string } | null;
};

export type TaskStatus = "open" | "overdue" | "completed";

export type OppTask = {
  id: string;
  contact_id: string;
  title: string;
  description: string | null;
  task_type: string;
  timezone: string | null;
  status: TaskStatus;
  due_at: string | null;
  due_date: string | null;
  due_time: string | null;
  reminder_minutes: number | null;
  reminder_at: string | null;
  reminder_sent_at: string | null;
  completed_at: string | null;
  created_at: string | null;
};

export type Note = {
  id: string;
  body: string;
  author: string | null;
  created_at: string;
  edited_at: string | null;
  edited_by: string | null;
  archived_at: string | null;
  revisions: number;
  legacy: boolean;
};

export type NoteRevision = { body: string; replaced_at: string; replaced_by: string | null };

export type ActivityItem = { id: string; type: string; summary: string; at: string; actor: string; data: Record<string, unknown> };

export type AppNotification = {
  id: string;
  kind: "task_reminder" | "task_overdue" | "security";
  title: string;
  body: string | null;
  contact_id: string | null;
  task_id: string | null;
  task_type: string | null;
  contact_name: string | null;
  due_at: string | null;
  timezone: string | null;
  task_status: TaskStatus | "deleted";
  created_at: string;
  fire_at: string;
  read: boolean;
};

export type Suppression = { id: string; email: string; reason: string; source: string | null; created_at: string };

export type EventSummary = Record<
  | "attempted" | "accepted" | "failed" | "bounced" | "replied" | "positive_reply" | "negative_reply"
  | "unsubscribed" | "complaint" | "meeting_booked" | "auto_reply",
  number
> & { reply_rate: number; positive_reply_rate: number; bounce_rate: number; unsubscribe_rate: number; failure_rate: number };

export type Analytics = {
  timezone: string;
  days: number;
  summary: EventSummary;
  daily: { date: string; accepted: number; replied: number; positive_reply: number; bounced: number; unsubscribed: number; failed: number }[];
  per_campaign: (EventSummary & { campaign_id: string; name: string })[];
  per_mailbox: (EventSummary & { email_account_id: string; email: string })[];
  funnel: { stage: LeadStatus; count: number }[];
  has_data: boolean;
};

export type SettingsResponse = {
  sections: {
    company: { name: string; website: string; phone: string };
    sending_identity: { default_from_name: string; reply_to: string };
    compliance: { physical_address: string; footer_template: string; list_unsubscribe_header: boolean; suppress_on_unsubscribe_reply: boolean };
    defaults: { daily_limit_per_mailbox: number; campaign_daily_limit: number; min_interval_seconds: number; schedule: Schedule; timezone: string };
    ai: { enabled: boolean; model: string; daily_token_budget: number; max_retries: number; classify_replies: boolean };
    notifications: { notify_on_reply: boolean; notification_email: string; notify_on_mailbox_error: boolean };
    [key: string]: Record<string, unknown>;
  };
  integrations: {
    google_oauth_configured: boolean;
    microsoft_oauth_configured: boolean;
    openai_configured: boolean;
    verification_provider: string;
    neverbounce_configured: boolean;
    sandbox_enabled: boolean;
    redis_configured: boolean;
    app_env: string;
    public_url: string;
  };
  compliance_problems: string[];
};

export type EnrollStatus = "ready" | "missing_fields" | "already_enrolled" | "suppressed" | "invalid_email" | "closed_stage" | "not_found";

export type EnrollReportRow = {
  contact_id: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  lead_status?: LeadStatus;
  status: EnrollStatus;
  missing: string[];
};

export type EnrollDryRun = {
  dry_run: true;
  summary: Record<EnrollStatus, number>;
  leads: EnrollReportRow[];
  campaign_status: Campaign["status"];
};

export type LeadPreview = {
  contact: EnrollReportRow;
  footer_problem: string | null;
  steps: {
    position: number;
    delay_days: number;
    business_days: boolean;
    threaded: boolean;
    ok: boolean;
    subject: string | null;
    text: string | null;
    html: string | null;
    missing: string[];
  }[];
};
