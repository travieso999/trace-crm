/* Trace CRM attribution for the Ellison & Reyes demo (first-party: serve this file from the demo's own site).
 *
 * A visitor who arrives through a tracked campaign link has ?trc=<random session token> on the URL. This script:
 *   1. keeps that token in the demo site's own localStorage for 30 days (no cookies, nothing third-party);
 *   2. removes it from the address bar at once, so it is never bookmarked, shared or sent as a referrer;
 *   3. reports step events (page viewed, application started, step reached, signed, submitted) to the CRM.
 *
 * Only event names, the page id, the wizard step number and two yes/no flags are sent. Nothing the visitor types,
 * no ID photos and no signature image ever leave the page through this script. Visitors without a token (anyone
 * who found the demo another way) send nothing at all.
 *
 * Load it in <head>, BEFORE the demo's main script: route() rewrites the URL on load and would drop ?trc= first.
 */
(function () {
  "use strict";
  var ENDPOINT = "https://crm.trace-studio.tech/api/track/demo";
  var KEY = "trace_attr", PARAM = "trc", DAYS = 30, TOKEN = /^[A-Za-z0-9_-]{16,64}$/;

  function load() {
    try {
      var v = JSON.parse(localStorage.getItem(KEY) || "null");
      if (v && TOKEN.test(v.t) && v.exp > Date.now()) return v.t;
      localStorage.removeItem(KEY);
    } catch (e) { /* storage blocked: attribution simply doesn't happen */ }
    return null;
  }
  function save(t) {
    try { localStorage.setItem(KEY, JSON.stringify({ t: t, exp: Date.now() + DAYS * 864e5 })); } catch (e) {}
  }

  try {
    var url = new URL(location.href), fresh = url.searchParams.get(PARAM);
    if (fresh && TOKEN.test(fresh)) save(fresh); // a newer campaign click replaces an older one
    if (url.searchParams.has(PARAM)) {
      url.searchParams.delete(PARAM);
      history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    }
  } catch (e) {}

  var token = load(), sent = {};

  function send(event, extra) {
    if (!token) return;
    var payload = { t: token, e: event };
    for (var k in extra || {}) if (Object.prototype.hasOwnProperty.call(extra, k)) payload[k] = extra[k];
    var body = JSON.stringify(payload);
    try { // text/plain: a "simple" request, so no preflight and it survives the page closing
      if (navigator.sendBeacon && navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "text/plain" }))) return;
    } catch (e) {}
    try {
      fetch(ENDPOINT, { method: "POST", mode: "cors", credentials: "omit", keepalive: true,
        headers: { "Content-Type": "text/plain" }, body: body }).catch(function () {});
    } catch (e) {}
  }
  function once(key, event, extra) { if (!sent[key]) { sent[key] = 1; send(event, extra); } }
  var slug = function (s) { return String(s || "").toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 40); };

  window.TraceDemo = {
    active: !!token,
    /** call from route(): TraceDemo.page(id) */
    page: function (id) { once("page:" + slug(id), "page_view", { page: slug(id) }); },
    /** call from showSt(): TraceDemo.step(st) — st is the 0-based wizard step */
    step: function (n) {
      n = Number(n) || 0;
      once("started", "application_started", { page: "start", form: "application" });
      if (n > 0) once("step:" + n, "application_step", { page: "start", form: "application", step: n });
    },
    /** first keystroke in the application wizard counts as starting it */
    started: function () { once("started", "application_started", { page: "start", form: "application" }); },
    /** call from notify(kind, fields, sample): reports that a demo form was submitted (never its fields) */
    form: function (kind, opts) {
      opts = opts || {};
      var sample = !!opts.sample;
      if (kind === "application") {
        if (opts.signed) once("signed", "signature_completed", { page: "start", form: "application" });
        once("submitted", "application_submitted", { page: "start", form: "application", sample: sample, signed: !!opts.signed });
      } else {
        once("form:" + slug(kind), "form_submitted", { form: slug(kind), sample: sample });
      }
    }
  };
})();
