export type Page<T> = { items: T[]; total: number; page: number; page_size: number };

export type Role = "admin" | "member" | "viewer";

export type User = {
  id: string;
  email: string;
  username: string | null;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  permissions: string[];
  permission_overrides: Record<string, boolean>;
  created_at: string | null;
  password_changed_at: string | null;
  mfa_enabled: boolean;
  mfa_method?: "totp" | "email";
};

/** A teammate as every signed-in user sees them (for owner pickers and filters). */
export type TeamMember = { id: string; full_name: string; username: string | null; email: string; role: Role; is_active: boolean };

export type PermissionCatalog = {
  permissions: { key: string; label: string; description: string; adjustable: boolean }[];
  roles: { key: Role; label: string; description: string; permissions: string[] }[];
};

/** Who an opportunity is assigned to (or who did something), as shown on cards and history. */
export type PersonRef = { id: string; name: string; is_active: boolean };

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
  missing_fields?: ("first_name" | "company_name")[];
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
  provider: "gmail" | "microsoft" | "sandbox" | "smtp";
  email: string;
  display_name: string | null;
  status: "connected" | "paused" | "auth_error" | "error" | "disconnected";
  daily_limit: number;
  min_interval_seconds: number;
  schedule: Schedule;
  signature_html: string | null;
  signature_text: string;
  signature_link: string | null;
  signature_image_url: string | null;
  signature_image_name: string | null;
  last_synced_at: string | null;
  last_sent_at: string | null;
  consecutive_failures: number;
  circuit_open_until: string | null;
  last_error: string | null;
  last_error_at: string | null;
  token_expires_at: string | null;
  scopes: string | null;
  /** smtp mailboxes only; the password is never returned */
  server: SmtpServer | null;
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
  name?: string | null;
  /** draft and paused emails hold prospects at this point of the sequence; nothing is skipped */
  status?: "draft" | "active" | "paused";
  /** not sent before this moment (in addition to the delay) */
  send_at?: string | null;
  video_asset_id?: string | null;
  /** include the clickable preview */
  video_preview?: boolean;
  /** attach the actual MP4 file */
  video_attach?: boolean;
  /** this email's own thumbnail; otherwise the one generated from the video */
  video_thumbnail_id?: string | null;
  /** text of the bar under the thumbnail */
  video_cta_text?: string | null;
};

export type VideoThumbnail = {
  id: string;
  filename: string;
  width: number | null;
  height: number | null;
  size_bytes: number;
  url: string;
};

export type VideoAsset = {
  id: string;
  filename: string;
  size_bytes: number;
  status: string;
  link_url: string | null;
  has_thumbnail: boolean;
  thumbnail_url: string | null;
  warnings: string[];
  created_at: string;
};

export type VideoLibrary = {
  items: VideoAsset[];
  limits: {
    max_upload_bytes: number;
    warn_bytes: number;
    library_max_bytes: number;
    library_used_bytes: number;
    chunk_bytes: number;
    providers: Record<string, { message_limit_bytes: number; max_attachment_bytes: number }>;
  };
};

export type AttachmentInfo = { filename: string; size_bytes: number; content_type: string; encoded_bytes: number };

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
  /** finished the sequence without replying: who a follow-up added to a completed campaign goes to */
  awaiting_follow_up?: number;
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
  /** this campaign's emails wait this many days after another campaign emailed the prospect (0 = off) */
  cross_campaign_cooldown_days: number;
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
  paused_at: string | null;
  campaign_type: string | null;
  target_location: string | null;
  industry: string | null;
  audience_mode: "fixed" | "dynamic";
  audience_filter: AudienceFilter;
  import_lists: { id: string; name: string }[];
};

/** Prospect filters (smart filters / dynamic audiences). Empty values are ignored. */
export type AudienceFilter = {
  q?: string;
  lead_status?: string;
  verification_status?: string;
  state?: string;
  city?: string;
  industry?: string;
  tag?: string;
  batch_id?: string;
  campaign_id?: string;
  not_in_campaign_id?: string;
  participation?: "never" | "previous";
  custom?: Record<string, string>;
};

export type ImportList = {
  id: string;
  name: string;
  original_filename: string | null;
  file_format: string | null;
  created_at: string;
  uploaded_by: string | null;
  total_rows: number;
  contacts: number;
  valid_contacts: number;
  duplicates: number;
  stats: Record<string, number>;
  campaigns: { id: string; name: string; status: Campaign["status"] }[];
};

export type RecipientSummary = {
  total: number;
  valid_emails: number;
  invalid_emails: number;
  duplicate_addresses: number;
  unsubscribed: number;
  suppressed: number;
  replied: number;
  paused: number;
  finished_or_stopped: number;
  /** blocked by the sender: empty merge fields (e.g. {{first_name}}) and no fallback */
  missing_data: number;
  missing_variables: Record<string, number>;
  eligible: number;
  cooldown_days: number;
  /** eligible, but another campaign emailed them within the cooldown (the last 14 days when it is off): they wait */
  recently_contacted: number;
  /** eligible, and still waiting in another running campaign (not counted in recently_contacted) */
  in_other_campaigns: number;
};

export type StepAudience = {
  step_id: string;
  position: number;
  already_sent: number;
  waiting: number;
  excluded: Record<string, number>;
  sample: { contact_id: string; email: string; first_name: string | null; last_name: string | null; company_name: string | null; next_send_at: string | null }[];
};

export type StepReport = {
  step_id: string;
  position: number;
  name: string | null;
  subject: string;
  body: string;
  status: "draft" | "active" | "paused";
  state: "draft" | "paused" | "sent" | "sending" | "scheduled" | "waiting";
  delay_days: number;
  business_days: boolean;
  send_at: string | null;
  next_send_at: string | null;
  first_sent_at: string | null;
  last_sent_at: string | null;
  recipients: number;
  waiting: number;
  queued: number;
  sent: number;
  failed: number;
  bounced: number;
  delivered: number;
  replied: number;
  positive: number;
  unsubscribed: number;
};

export type StepMessage = {
  contact_id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  status: string;
  error: string | null;
  due_at: string | null;
  sent_at: string | null;
  subject: string | null;
  campaign_status: string;
  replied_at: string | null;
};

export type HistoryItem = {
  direction: "out" | "in" | "pending" | "failed" | "cancelled";
  kind: string;
  at: string | null;
  subject: string | null;
  body_html?: string;
  body_text?: string | null;
  from_email?: string;
  to_email?: string;
  intent?: string | null;
  error?: string | null;
  step: number | null;
};

export type CampaignAnalytics = {
  total_contacts: number;
  contacts_emailed: number;
  emails_sent: number;
  failed: number;
  queued: number;
  bounced: number;
  delivered: number;
  replies: number;
  positive_replies: number;
  unsubscribes: number;
  followups_sent: number;
  awaiting_followup: number;
  not_started: number;
  paused: number;
  completed_sequence: number;
  removed: number;
  rates: Record<"delivery_rate" | "bounce_rate" | "reply_rate" | "positive_reply_rate" | "unsubscribe_rate", number | null>;
  definitions: Record<string, string>;
};

export type VideoFields = Pick<Step, "video_asset_id" | "video_preview" | "video_attach" | "video_thumbnail_id" | "video_cta_text">;

export type Template = VideoFields & { id: string; name: string; subject: string; body: string; created_at: string };

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

export type SmtpServer = {
  smtp_host: string;
  smtp_port: number;
  smtp_security: "ssl" | "starttls";
  imap_host: string;
  imap_port: number;
  username?: string;
};

type ProviderInfo = {
  available: boolean;
  configured: boolean;
  reason: string | null;
  outbox_dir?: string | null;
  redirect_uri?: string;
  scopes?: string[];
  presets?: Record<string, Omit<SmtpServer, "username">>;
};

export type MailProviders = Record<"gmail" | "microsoft" | "sandbox" | "smtp", ProviderInfo> & {
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
  phone: string | null;
  state: string | null;
  city: string | null;
  verification_status: string;
  suppressed: boolean;
  last_sent_at: string | null;
  paused_at: string | null;
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
  attachment?: AttachmentInfo | null;
  attachment_warnings?: string[];
  preview_link_missing?: boolean;
  signature_missing?: string[];
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
  owner: PersonRef | null;
};

export type OppRow = OppCard & { last_activity_at: string | null; created_at: string | null };

export type OppList = { items: OppRow[]; total: number; value_cents: number; page: number; page_size: number };

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
  accountability: {
    created_by: PersonRef | null;
    opportunity_created_at: string | null;
    updated_by: PersonRef | null;
    last_edited_at: string | null;
    owner_assigned_by: PersonRef | null;
    owner_assigned_at: string | null;
  };
  /** What the signed-in user may do here (the API enforces the same rules). */
  can?: { edit: boolean; remove: boolean; assign_any: boolean; take: boolean; release: boolean };
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
  created_by: string | null;
  assignee: string | null;
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
  kind: "task_reminder" | "task_overdue" | "security" | "mailbox" | "assignment" | "demo_completed" | "suppression"
    | "callback_request";
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
    video_attachments: { max_attachment_mb: number; warn_attachment_mb: number; library_max_mb: number };
    compliance: { physical_address: string; footer_template: string; list_unsubscribe_header: boolean; suppress_on_unsubscribe_reply: boolean };
    defaults: { daily_limit_per_mailbox: number; campaign_daily_limit: number; min_interval_seconds: number; schedule: Schedule; timezone: string };
    ai: { enabled: boolean; model: string; daily_token_budget: number; max_retries: number; classify_replies: boolean };
    notifications: { notify_on_reply: boolean; notification_email: string; notify_on_mailbox_error: boolean };
    tracking: { click_tracking: boolean; open_tracking: boolean; demo_urls: string[]; demo_origins: string[];
                scanner_window_seconds: number; retention_days: number };
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
  /** not a skip reason: added, but another campaign got there first (or will), so emails are spaced out */
  cooldown?: { kind: "recently_contacted" | "in_other_campaign"; until: string | null; message: string } | null;
};

export type EnrollDryRun = {
  dry_run: true;
  summary: Record<EnrollStatus | "recently_contacted" | "in_other_campaign", number>;
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
    attachment: AttachmentInfo | null;
  }[];
};

// --- engagement tracking ---------------------------------------------------------------------------

export type TrackingSettings = { click_tracking: boolean; open_tracking: boolean; demo_urls: string[]; retention_days: number };

export type TrackedLinkStat = {
  id: string; kind: "link" | "demo"; label: string | null; url: string | null; clicks: number; scanner_clicks: number;
  first_click_at: string | null; last_click_at: string | null;
};

export type EmailTrackingRow = {
  sent_message_id: string; contact_id: string; name: string; email: string; company: string | null;
  campaign_id: string | null; campaign: string | null; step: number | null; subject: string; sent_at: string;
  delivery: "accepted" | "bounced"; replied_at: string | null; tracked: boolean; opens: number | null;
  clicks: number; unique_clicks: number; scanner_clicks: number; demo_clicks: number;
  first_click_at: string | null; last_click_at: string | null;
  demo_visits: number; demo_interactions: number; demo_completed: number; watched_video: number; links: TrackedLinkStat[];
};

export type EmailTrackingPage = Page<EmailTrackingRow> & {
  summary: { emails: number; tracked: number; clicked: number; demo_clicked: number; demo_visited: number;
             demo_completed: number; scanner_clicks: number };
  settings: TrackingSettings;
};

export type EngagementLevel = "high" | "medium" | "low";

export type EngagedProspect = {
  contact_id: string; name: string; email: string; company: string | null; lead_status: string; level: EngagementLevel;
  clicks: number; demo_clicks: number; opens: number; demo_visits: number; demo_interactions: number;
  demo_completed: number; watched_video: number; replies: number; callback_requested_at: string | null;
  last_engagement_at: string | null; campaigns: { id: string; name: string }[];
  owner: { id: string; name: string } | null; open_tasks: number; hidden_at: string | null;
};

export type EngagedPage = Page<EngagedProspect> & {
  levels: Record<EngagementLevel, number>; hidden: number; settings: TrackingSettings;
};

export type TimelineItem = {
  kind: "email_sent" | "email_opened" | "email_click" | "demo_click" | "demo_visit" | "demo_application_started"
    | "demo_application_completed" | "demo_form" | "email_replied"
    | "site_visit" | "site_demo_opened" | "site_video_watched" | "site_application_started" | "site_application_completed"
    | "site_callback_request" | "site_contact_tap" | "site_demo_form" | "site_identify";
  at: string; summary: string; campaign_id: string | null; campaign: string | null;
  url?: string | null; count?: number; sample?: boolean | null; signed?: boolean | null;
  site?: string | null; page?: string | null;
};

/** One step of a website visit, in plain words ("Watched 75% of the video"). */
export type SiteStep = {
  at: string; type: string; text: string; site: string | null; page: string | null; strong: boolean;
  seconds: number | null; scroll: number | null;
};

/** One visit (website session) to trace-studio.tech: steps in the order they happened. */
export type SiteVisit = {
  sid: string; started_at: string; ended_at: string; sites: string[]; city: string | null; region: string | null;
  country: string | null; device: string | null; active_seconds: number | null; steps: SiteStep[]; events: number;
};

export type SiteTrackingStatus = {
  endpoint: string; configured: boolean; secret: string | null; last_received_at: string | null; events: number;
  visitors: number; matched_visitors: number;
  unmatched: { vid: string; rid: string | null; name: string | null; email: string | null; phone: string | null;
               city: string | null; region: string | null; device: string | null; events: number; last_seen_at: string }[];
};

export type RawTrackingEvent = {
  source: "email" | "demo" | "site"; type: string; at: string; campaign: string | null; user_agent: string | null;
  site?: string | null; place?: string | null; data?: Record<string, unknown>;
  link?: string | null; url?: string | null; suspected_bot?: boolean; verified_human?: boolean; reasons?: string[];
  page?: string | null; form?: string | null; step?: number | null; sample?: boolean | null; signed?: boolean | null;
};

export type EngagementTimeline = {
  items: TimelineItem[]; level: EngagementLevel | null; visits: SiteVisit[]; raw?: RawTrackingEvent[];
};

export type ContactOutreach = {
  campaigns: {
    campaign_id: string;
    name: string;
    campaign_status: Campaign["status"];
    status: string;
    emails_sent: number;
    total_steps: number;
    added_at: string;
    next_send_at: string | null;
    stop_reason: string | null;
    replied_at: string | null;
    cooldown_days: number;
  }[];
  /** newest first */
  emails: {
    id: string;
    sent_at: string;
    kind: "sequence" | "manual_reply";
    subject: string;
    from_email: string;
    campaign_id: string | null;
    campaign_name: string | null;
    step: number | null;
    /** days since the previous email from a different campaign */
    gap_days: number | null;
    previous_campaign_name: string | null;
    too_close: boolean;
  }[];
};
