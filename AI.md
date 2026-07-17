# AI Computer Setup — HelpfulDjinn AI Assistant

This document is a complete, step-by-step guide for setting up the dedicated AI computer
that powers the helpdesk's AI features (similar-ticket search and AI-suggested responses).
It is written so a person — or an AI assistant running on that machine — can follow it
end-to-end without any other context.

## What the AI computer does

The helpdesk talks to this machine over HTTP using [Ollama](https://ollama.com):

| Helpdesk feature | What it calls | Model used |
|---|---|---|
| Ticket similarity index ("Similar Tickets" panel) | `POST /api/embed` | `nomic-embed-text` |
| AI Suggested Response drafts | `POST /api/chat` | `qwen2.5:14b` (default) |
| Admin "Test Connection" button | `GET /api/tags` | — |

The ticket index itself (the vectors) is stored in the helpdesk's own database — this
machine only computes embeddings and drafts replies. If it goes offline, the helpdesk
keeps working: similar-ticket search still runs from stored vectors; only new indexing
and new suggestions pause until it's back.

**Speed does not matter much.** Suggested responses are generated in the background by
the helpdesk scheduler shortly after tickets arrive, so a reply taking 1–3 minutes to
draft is fine. Model choice below prioritizes answer quality over speed.

## Target hardware (this guide's assumptions)

- CPU: Intel Xeon W-2155 (10 cores / 20 threads, 3.3 GHz)
- RAM: 32 GB
- GPU: NVIDIA Quadro P4000 — **8 GB VRAM**, Pascal architecture
- OS: Windows 10/11 (64-bit)

What the hardware means for model choice:
- Models up to ~7 GB run fully on the GPU (fast).
- Bigger models split between GPU and system RAM automatically (slower, still fine here).
- 32 GB RAM comfortably fits quantized models up to the ~30B-parameter class.

---

## Step 1 — Install the NVIDIA driver

1. Download the current **NVIDIA RTX / Quadro driver** for the Quadro P4000 (Windows 10/11 64-bit)
   from https://www.nvidia.com/drivers (Product series: "NVIDIA RTX / Quadro", Product: "Quadro P4000").
2. Install it and reboot.
3. Verify: open PowerShell and run `nvidia-smi`. You should see the P4000 listed with 8192 MiB memory.
   (Ollama bundles its own CUDA runtime — you do NOT need to install the CUDA Toolkit separately.)

## Step 2 — Install Ollama

1. Download the Windows installer from https://ollama.com/download/windows and run it.
2. After install, Ollama runs as a background/tray app and starts automatically at login.
3. Verify in PowerShell:
   ```powershell
   ollama --version
   curl http://localhost:11434/api/tags
   ```
   The curl should return JSON (an empty model list at first).

## Step 3 — Make Ollama reachable from the helpdesk server

By default Ollama only listens on `localhost`. If the helpdesk runs on a **different**
machine, expose Ollama on the LAN:

1. Set system environment variables (System Properties → Environment Variables → *System variables* → New,
   or in an **admin** PowerShell):
   ```powershell
   [Environment]::SetEnvironmentVariable('OLLAMA_HOST', '0.0.0.0:11434', 'Machine')
   [Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE', '30m', 'Machine')
   ```
   - `OLLAMA_HOST=0.0.0.0:11434` — listen on all interfaces so the helpdesk can connect.
   - `OLLAMA_KEEP_ALIVE=30m` — keep the model loaded for 30 minutes after each request,
     so back-to-back tickets don't pay the (slow) model-load cost every time.
2. Quit Ollama from the system tray and start it again (or reboot) so the variables take effect.
3. Add a firewall rule allowing inbound TCP 11434 **on the private profile only** (admin PowerShell):
   ```powershell
   New-NetFirewallRule -DisplayName "Ollama (HelpDesk AI)" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow -Profile Private,Domain
   ```
4. Give this machine a **static IP or DHCP reservation** on your router so the helpdesk's
   configured address never changes. Note the IP (`ipconfig`).
5. Verify from the **helpdesk server**: `curl http://<AI-COMPUTER-IP>:11434/api/tags`.

> **Security note:** Ollama has **no authentication**. Anyone who can reach port 11434 can
> use the models. Keep the rule limited to the private/domain firewall profiles, never
> port-forward 11434 through your router, and keep both machines on the same trusted LAN.
> (If the helpdesk runs on this same machine, skip this whole step and use `localhost`.)

## Step 4 — Pull the models

In PowerShell on the AI computer:

```powershell
# Required: embedding model for the ticket similarity index (~274 MB, runs on GPU easily)
ollama pull nomic-embed-text

# Recommended default chat model (~9 GB download)
ollama pull qwen2.5:14b
```

### Chat model options for this hardware (quality first)

| Model | Size (Q4) | Fit on P4000 + 32 GB | Approx. speed | Notes |
|---|---|---|---|---|
| `qwen2.5:14b` **(default)** | ~9 GB | Mostly GPU, small CPU spill | ~4–6 tok/s | Best quality-per-minute for this box. Strong instruction following, good technical writing. |
| `qwen2.5:32b` | ~20 GB | Mostly CPU/RAM | ~1–2 tok/s | Noticeably better reasoning/quality. A reply takes 2–5 minutes — acceptable because drafts are generated in the background. Use this if you want maximum quality. |
| `phi4:14b` | ~9 GB | Mostly GPU | ~4–6 tok/s | Alternative 14B with strong reasoning; try if Qwen's tone doesn't suit you. |
| `llama3.1:8b` | ~4.9 GB | Fully GPU | ~15–25 tok/s | Fastest option; use only if you later decide speed matters more than quality. |

To switch models later: `ollama pull <model>` here, then change **Chat model** on the
helpdesk's Admin → AI Assistant page. No other changes needed.

### Warm-up test

```powershell
ollama run qwen2.5:14b "Write a two-sentence friendly IT helpdesk reply asking a user to reboot their computer."
```

First run loads the model (can take a minute or two); after that it should answer.
Type `/bye` to exit.

## Step 5 — Connect the helpdesk

1. In the helpdesk, go to **Admin → AI Assistant** (`/admin/ai`).
2. Enter:
   - **AI server host / IP**: the AI computer's IP (or `localhost` if same machine)
   - **Port**: `11434`
   - **Chat model**: `qwen2.5:14b` (or your choice from the table)
   - **Embedding model**: `nomic-embed-text`
3. Click **Test Connection** — it should report success and list the installed models.
   It also warns if a configured model isn't pulled yet.
4. Tick **Enable AI assistant**, and (recommended) **Auto-generate suggested responses
   for new tickets**, then **Save Settings**.
5. The helpdesk **scheduler process** does the indexing and background drafting — make
   sure it is running (`HELPFULDJINN_ROLE=scheduler python Source/scheduler_run.py`, or
   your service equivalent). Within one index interval (default 10 minutes) the
   "Index status" on the AI settings page should show tickets being embedded.

## What to expect once connected

- **Similar Tickets**: every ticket detail page gets a right-side panel listing the most
  similar past tickets with a match percentage — click through to see how they were handled.
- **AI Suggested Response**: above the Add Note box, a drafted reply appears (automatically
  for new tickets if auto-generate is on, or via the button). **Use in reply** copies it
  into the note editor for editing and sending; **Regenerate** re-drafts; **Dismiss** hides it.
- Access is controlled per-role via Admin → Roles & Permissions → "AI Assistant".

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Test Connection: "connection refused" | Ollama not running, or listening on localhost only | Check tray app is running; re-check `OLLAMA_HOST` (Step 3) and restart Ollama |
| Test works on the AI computer but not from the helpdesk | Firewall | Re-run the firewall rule in Step 3; confirm both machines are on the same network/profile |
| Test warns a model is "not installed" | Model not pulled | `ollama pull <model>` on the AI computer |
| Replies take 5+ minutes or time out | Model too large / cold load | Use `qwen2.5:14b` instead of 32b; set `OLLAMA_KEEP_ALIVE=30m`; first request after idle is always slowest |
| `nvidia-smi` shows 0% GPU during generation | Model spilled entirely to CPU or driver issue | Expected for 32b-class models; for 14b, update the NVIDIA driver and restart Ollama |
| Index status shows an error on the AI settings page | AI box was unreachable during an index run | Fix connectivity; the next scheduled run resumes automatically |
| Suggestions read oddly / wrong tone | Model choice | Try `phi4:14b` or `qwen2.5:32b`; regenerate on a few tickets to compare |

## Maintenance

- **Updates**: Ollama self-updates on Windows; models update only when you `ollama pull` again.
- **Disk**: models live in `%USERPROFILE%\.ollama\models`. `ollama list` shows installed
  models, `ollama rm <model>` removes ones you no longer use.
- **Rebuilding the index**: after changing the embedding model, use **Rebuild Full Index**
  on the helpdesk AI settings page (all tickets are re-embedded on the next index run).
