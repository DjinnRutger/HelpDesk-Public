# HelpfulDjinn — Helpdesk Server Spec: Client Ticket Intake + Admin "Client API" Settings

**Audience:** the AI/developer working on the self-hosted **HelpfulDjinn** helpdesk.
**Goal:** add (1) an **intake endpoint** that receives screenshot tickets from the *HelpfulDjinn
Client* desktop app, and (2) an **admin "System → Client API" settings page** (a button in the
admin area) that lets an administrator configure *how* the helpdesk receives that API and generate
the credentials the client needs.

This document is the **authoritative contract**. The desktop client is already built and sends
exactly what's described here — match it on the server side. Adapt the code to whatever framework
HelpfulDjinn uses (Node/Express, Laravel, Django, ASP.NET, etc.); the wire format is fixed.

---

## 1. Background

The desktop client runs in the system tray on ~80 Windows PCs over a **Netbird** zero-trust network.
When a user presses a hotkey (default `Ctrl+Alt+D`), it captures a screenshot + machine specs and
**POSTs them to the helpdesk** to open a ticket. The helpdesk is reachable over Netbird via internal
DNS or a `100.x` address.

Two pieces are needed on the helpdesk:

1. **Intake API** — receives the submission and creates a ticket with the screenshot attached.
2. **Admin settings page** — lets IT turn intake on/off, choose the auth scheme, and generate/rotate
   the token. The page should also **show the exact values to paste into the client's Settings**
   (Endpoint URL, Auth scheme, Header name, Token) so the two sides stay in sync.

---

## 2. Intake API contract (fixed — implement to match)

### 2.1 Request

- **Method:** `POST`
- **URL:** `{ApiBaseUrl}{SubmitPath}` — client default `https://helpdesk.netbird.internal/api/tickets`
  (both parts are configurable in the client; pick a stable path like `/api/tickets` and tell IT to
  set it in the client).
- **Transport:** HTTPS for internet-exposed deployments. **HTTP is acceptable when the
  endpoint is confined to the Netbird interface** (no public access) — which is how this
  helpdesk is currently deployed, so point the client's `ApiBaseUrl` at an `http://…`
  address. The server has an optional `Require HTTPS` toggle (off by default).
- **Content-Type:** `multipart/form-data` with **two parts**:

| Part name    | Type                 | Filename        | Contents                                              |
|--------------|----------------------|-----------------|-------------------------------------------------------|
| `payload`    | `application/json`   | —               | Ticket metadata + system info (schema in §2.3)        |
| `screenshot` | `image/png`          | `screenshot.png`| Raw PNG bytes of the screenshot                       |

> The screenshot may be large (full multi-monitor desktop). Allow uploads up to ~25 MB.

> **⚠️ The `screenshot` part MUST be a file part with a `filename` (action required for the client).**
> Its `Content-Disposition` header must include a `filename`, e.g.:
> `Content-Disposition: form-data; name="screenshot"; filename="screenshot.png"`.
> If the filename is omitted, servers (Flask/Werkzeug ≥ 3.1) treat the part as an in-memory
> form field, which (a) **rejects anything over 500 KB with `413 Request Entity Too Large`**
> regardless of the configured max-upload size, and (b) corrupts the binary so it can't be
> stored. Most HTTP libraries set the filename automatically when you add the part as a file
> upload (e.g. .NET `MultipartFormDataContent` → `Add(new StreamContent(...), "screenshot",
> "screenshot.png")`; `ByteArrayContent` with `ContentDisposition.FileName = "screenshot.png"`
> and `ContentType = image/png`). The helpdesk now also raises the in-memory limit defensively,
> but a missing filename will still prevent the screenshot from being attached — **set the
> filename.** This is the cause of the `413` seen during early testing.

### 2.2 Authentication

The client sends **one** of these, depending on how the admin configured it. The server must accept
the scheme the admin selected on the settings page:

| Scheme (client `Auth.Scheme`) | What the client sends                                  |
|-------------------------------|--------------------------------------------------------|
| `Bearer`                      | `Authorization: Bearer <token>`                        |
| `ApiKeyHeader`                | `<HeaderName>: <token>` (e.g. `X-Api-Key: <token>`)    |
| `None`                        | No auth header (lab/testing only — discourage in prod) |

- The token is an opaque string the **admin page generates** (see §4).
- Reject requests with a missing/invalid token: **`401 Unauthorized`**.
- **Recommended default:** `Bearer` with a long random token.

### 2.3 `payload` JSON schema

Property names are **camelCase** exactly as below (the client serializes with camelCase). Treat any
field as best-effort — the client may send empty/zero values if a probe failed, so **do not hard-fail
on missing sub-fields**.

```jsonc
{
  "title":       "string",   // user-entered; may be empty
  "description": "string",   // user-entered; may be empty
  "source":      "DjinnWish",// constant identifying the client module
  "systemInfo": {
    "computerName":      "WS-SHOP-07",
    "userName":          "jdoe",
    "userEmail":         "jdoe@company.com",              // signed-in user's email / UPN; see note below
    "ipAddresses":       ["10.4.2.18", "100.92.0.14"],   // all non-loopback IPv4s
    "osVersion":         "Windows 11 Pro (23H2)",
    "osBuild":           "22631.3737",
    "lastBootTimeLocal": "2026-06-05T07:14:22",            // local time, ISO 8601
    "uptime":            "3d 4h 51m",
    "cpuModel":          "Intel(R) Core(TM) i5-10500 CPU @ 3.10GHz",
    "logicalProcessors": 12,
    "totalRamMb":        16312,
    "netbird": {
      "isInstalled": true,
      "isConnected": true,
      "netbirdIp":   "100.92.0.14",
      "summary":     "Connected (100.92.0.14)"
    },
    "clientVersion":  "0.1.0",
    "capturedAtUtc":  "2026-06-08T17:31:02.123Z"           // UTC, ISO 8601
  }
}
```

> **`userEmail` (please add to the client).** When present, the helpdesk uses it to
> **tie the ticket to an existing user** (it upserts a Contact by email, exactly like the
> email-intake path) and to set the ticket's requester. This is the key that will let a
> future client feature show the user the status of *their* tickets. Send the signed-in
> user's email / UPN when the client can determine it (e.g. from Azure AD / Entra join,
> `whoami /upn`, or a configured value). It is **best-effort** like every other field —
> if the client can't resolve it, omit it or send an empty string and the server falls
> back to recording `userName@computerName` as free text.

### 2.4 Response (what the client expects)

- **Success:** any **`2xx`** status. The client shows a "Ticket submitted successfully" toast and
  does **not** parse the body. Returning a small JSON body is still recommended for logging/future
  use:
  ```json
  { "ok": true, "ticketId": 1234, "ticketNumber": "HD-1234", "url": "https://.../tickets/1234" }
  ```
- **Failure:** any **non-2xx** (e.g. `401`, `413`, `422`, `500`). The client shows an error toast
  with click-to-retry and saves the ticket locally for resubmission. A JSON error body helps:
  ```json
  { "ok": false, "error": "Invalid token" }
  ```
- **Timeout:** the client aborts after its configured timeout (default 30 s). Keep processing fast;
  do heavy work (image processing, notifications) asynchronously after responding.

### 2.5 Reference request (for your tests)

```bash
curl -X POST "https://helpdesk.netbird.internal/api/tickets" \
  -H "Authorization: Bearer REPLACE_WITH_TOKEN" \
  -F 'payload={"title":"Printer offline","description":"Won''t print","source":"DjinnWish","systemInfo":{"computerName":"WS-SHOP-07","userName":"jdoe","ipAddresses":["100.92.0.14"],"osVersion":"Windows 11 Pro (23H2)","osBuild":"22631.3737","uptime":"3d 4h","cpuModel":"i5-10500","logicalProcessors":12,"totalRamMb":16312,"netbird":{"isConnected":true,"netbirdIp":"100.92.0.14","summary":"Connected"},"clientVersion":"0.1.0","capturedAtUtc":"2026-06-08T17:31:02Z"}};type=application/json' \
  -F 'screenshot=@screenshot.png;type=image/png'
```

### 2.6 Check-in endpoint (`POST /api/checkin`) — IMPLEMENTED

In addition to ticket intake, the client posts a lightweight **check-in every 5 minutes** so staff
can see when each machine was last online (shown on the User page). Same enable switch and token
auth as `/api/tickets`.

- **Method / URL:** `POST {ApiBaseUrl}/api/checkin`
- **Content-Type:** `application/json` (no file part)
- **Auth:** identical to §2.2 (Bearer / ApiKeyHeader / None)
- **Body:** the same `systemInfo` object as §2.3, wrapped:
  ```json
  { "source": "DjinnWish", "systemInfo": { "computerName": "...", "userEmail": "...", "ipAddresses": ["..."], "clientVersion": "...", "...": "..." } }
  ```
- **Server behavior:** match a `Contact` by `systemInfo.userEmail` (create if missing) and stamp
  `last_checkin_at` (UTC now), `last_checkin_computer`, `last_checkin_ip`, `last_checkin_client_version`.
- **Responses:** `200 {"ok":true,"matched":true}` (linked to a contact) / `{"ok":true,"matched":false}`
  (no email to link); `401` invalid token; `503` intake disabled; `422` malformed JSON.

The User page (`/users/<id>`) shows a **Computer Check-in** card: an Online/Offline badge
(online = checked in within 15 min) plus the last check-in time, computer, IP, and client version.

```bash
curl -X POST "http://helpfuldjinn.strobel.local/api/checkin" \
  -H "Authorization: Bearer REPLACE_WITH_TOKEN" -H "Content-Type: application/json" \
  -d '{"source":"DjinnWish","systemInfo":{"computerName":"WS-SHOP-07","userName":"jdoe","userEmail":"jdoe@company.com","ipAddresses":["100.92.0.14"],"clientVersion":"0.1.0"}}'
```

---

## 3. Server behavior to implement

For each valid POST:

1. **Authenticate** per the configured scheme (§2.2). Invalid → `401`.
2. **Validate** content type is multipart with `payload` + `screenshot` parts; enforce max size →
   `413` if too large; malformed JSON → `422`.
3. **Create a ticket:**
   - **Subject/Title:** use `title`. If empty, synthesize one, e.g.
     `"Screenshot ticket from {systemInfo.computerName} ({systemInfo.userName})"`.
   - **Requester:** map `systemInfo.userName` / `computerName` to a user/asset if your helpdesk
     supports it; otherwise store as text.
   - **Body/Description:** the user's `description`, followed by a rendered **System Information**
     block (table or key/value list) built from `systemInfo`. Include Netbird status prominently.
   - **Attachment:** save `screenshot.png` and attach it to the ticket (and/or embed inline in the
     first comment).
   - **Tag/source:** mark the ticket with `source = "DjinnWish"` so these are filterable.
   - **Raw metadata:** persist the full `systemInfo` JSON (custom field or internal note) for later
     querying.
4. **Respond** `2xx` quickly with the JSON body from §2.4.
5. **Log** the submission (token id used, machine, ticket id, size, result).

---

## 4. Admin page: "System → Client API" (the button to build)

Add an admin-only page (e.g. **Admin → System → Client API**, or a "Client API" button on the System
settings screen). It configures the intake and produces the credentials the client needs.

### 4.1 Settings to expose

| Setting                  | Type / control          | Notes                                                                 |
|--------------------------|-------------------------|-----------------------------------------------------------------------|
| **Intake enabled**       | toggle                  | Master on/off. When off, intake returns `503`.                        |
| **Endpoint path**        | read-only / text        | e.g. `/api/tickets`. Show the **full URL** for IT to copy.            |
| **Auth scheme**          | dropdown                | `Bearer` (recommended), `ApiKeyHeader`, `None`. Matches client.       |
| **Header name**          | text (if `ApiKeyHeader`)| Default `X-Api-Key`. Must match the client's `Auth.HeaderName`.       |
| **API tokens**           | list + "Generate" button| One or more tokens (see §4.2).                                        |
| **Max upload size (MB)** | number                  | Default 25.                                                           |
| **Default assignee/queue** (optional) | dropdown   | Where DjinnWish tickets land.                                         |
| **Auto-close / priority** (optional)  | controls   | Defaults for these tickets.                                           |

### 4.2 Token management

- **Generate token:** create a cryptographically random opaque string (≥ 32 bytes, base64url).
  Store **only a hash** server-side; show the **plaintext once** at creation for the admin to copy.
- Support **multiple tokens** with: label/name (e.g. "Fleet 2026"), created date, last-used date,
  and **Revoke**. This enables rotation and per-rollout keys without downtime.
- Optional niceties: per-token rate limit, expiry date, and an **allowed source CIDR**
  (e.g. restrict to the Netbird `100.64.0.0/10` range).

### 4.3 "Client configuration" helper (important)

After saving, the page should display a **copy-ready summary** of exactly what to enter in the
**client's Settings form**, so the two sides match. Render something like:

```
Point the HelpfulDjinn Client at this helpdesk using these Settings values:

  API base URL : https://helpdesk.netbird.internal
  Submit path  : /api/tickets
  Auth scheme  : Bearer
  Header name  : Authorization        (only used for "Api Key Header" scheme)
  Auth token   : <the token shown once at generation>
```

(These map 1:1 to the client's config: `Wish.ApiBaseUrl`, `Wish.SubmitPath`, `Wish.Auth.Scheme`,
`Wish.Auth.HeaderName`, `Wish.Auth.Token`.) A "Copy" button and/or a downloadable snippet is ideal.

---

## 5. Security requirements

- **HTTPS for internet-exposed deployments.** Plain HTTP is acceptable only when the
  endpoint is restricted to the Netbird interface/CIDR (the current deployment). An
  optional server-side `Require HTTPS` setting can enforce TLS where appropriate.
- Store tokens **hashed**, never in plaintext; compare in constant time.
- Validate `Content-Type`, part names, and **enforce the max upload size** before buffering the file.
- Treat `payload` JSON as untrusted: cap field lengths, sanitize before rendering into ticket
  HTML/markdown (the screenshot and free-text fields come from end users).
- Rate-limit per token / per source IP to prevent a stuck client from flooding tickets.
- Log auth failures; consider alerting on spikes.
- Prefer restricting the endpoint to the Netbird interface/CIDR at the network or app layer.

---

## 6. Acceptance criteria

- [ ] Admin page exists, is admin-only, and persists all settings in §4.1.
- [ ] Admin can **generate** a token (shown once), see it listed, and **revoke** it.
- [ ] Page shows the copy-ready **client configuration** block (§4.3) with the live full URL.
- [ ] `POST /api/tickets` accepts `multipart/form-data` with `payload` (JSON) + `screenshot` (PNG).
- [ ] Valid token → **`2xx`** and a new ticket with the screenshot attached and `systemInfo` rendered.
- [ ] Missing/invalid token → **`401`**; oversized upload → **`413`**; bad JSON → **`422`**;
      intake disabled → **`503`**.
- [ ] Response is fast (heavy work deferred) and returns the JSON body from §2.4.
- [ ] End-to-end test passes using the curl in §2.5 **and** the real desktop client (hotkey → ticket).

---

## 7. Future (design with these in mind, don't build yet)

The client is being extended for **central management over Netbird**. Plan for these so the admin
area can grow:

- **Config push** — helpdesk serves per-machine client config the client pulls on a schedule
  (e.g. `GET /api/clients/{machine}/config`).
- **Status/telemetry ingest** — clients report online status, version, and (policy-permitted) idle
  stats (`POST /api/clients/{machine}/status`).
- **Command channel** — helpdesk queues commands for clients to fetch/long-poll
  (e.g. "open settings", "apply drive maps", "submit diagnostics").

Reuse the **same token/auth model** from §4 for all of these so there's one consistent,
admin-managed credential per machine/fleet.

