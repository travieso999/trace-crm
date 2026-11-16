# Ellison & Reyes demo → Trace CRM attribution

The demo lives at `https://trace-studio.tech/demos/ellison-reyes` (it is not in this repository). These are the changes
it needs so that a visit that came from a campaign link shows up on the right prospect in the CRM.

## How attribution works

1. Every campaign email gets its own tracked links: `https://crm.trace-studio.tech/t/<random token>`.
2. A click on a demo link is recorded, then redirected to the demo with every original parameter kept plus
   `trc=<random demo-session token>`. Neither token contains anything about the recipient.
3. `trace-attribution.js` (below) keeps that token in the demo site's own `localStorage`, removes it from the address
   bar, and posts step events to `https://crm.trace-studio.tech/api/track/demo`.
4. The CRM accepts events only from the origins listed in Settings → Tracking (`https://trace-studio.tech` by default)
   and only for sessions it created. Unknown tokens are silently ignored.

Visitors who reach the demo any other way (search, a forwarded URL without `trc`, a direct visit) send nothing.

## Changes to make in the demo

1. Copy `trace-attribution.js` to `/demos/ellison-reyes/trace-attribution.js` on the demo site, so it is served
   first-party.
2. In `index.html`, load it in `<head>`, **before** the main inline script (`route()` rewrites the URL on load and
   would otherwise drop `?trc=` before it is read):

   ```html
   <script src="/demos/ellison-reyes/trace-attribution.js"></script>
   ```

3. Add one line to each of the three existing functions (each guarded, so the demo works unchanged without the file):

   ```js
   function route(){const id=pageFromUrl();CUR=id;window.TraceDemo&&TraceDemo.page(id); /* …existing code… */ }

   function showSt(){window.TraceDemo&&TraceDemo.step(st); /* …existing code… */ }

   function notify(kind,fields,sample){
     window.TraceDemo&&TraceDemo.form(kind,{sample:!!sample,signed:kind==="application"});
     /* …existing fetch("/api/demo-lead", …) unchanged… */
   }
   ```

   `signed:kind==="application"` relies on the wizard refusing to submit until the signature pad has ink, which is
   what the current flow does ("Sign & submit"). If that ever changes, pass the pad's real state instead.

   Optionally, count the first keystroke as starting the application:

   ```js
   $("#wiz").addEventListener("input",()=>window.TraceDemo&&TraceDemo.started(),{once:true});
   ```

4. If the demo is ever given a Content-Security-Policy with `connect-src`, add `https://crm.trace-studio.tech`.

## What is (and is not) sent

| Sent | Never sent |
|---|---|
| the random session token, event name (`page_view`, `application_started`, `application_step`, `signature_completed`, `application_submitted`, `form_submitted`), page id, wizard step number, form name, `sample` (example values left untouched) and `signed` flags | names, dates of birth, phone numbers, emails, booking numbers, bail amounts, free text, ID photos, the signature image, IP addresses (the CRM stores only a keyed hash for clicks), any cookie |

The existing `/api/demo-lead` notification email is unchanged.

## Privacy notice

Add a line to the demo's legal page, for example: *"If you arrived from one of our emails, we record which demo
pages and steps you used so we can follow up. We do not record what you type into the demo."* Prospects who
unsubscribe are no longer tracked: their clicks still open the page, but nothing is recorded.
