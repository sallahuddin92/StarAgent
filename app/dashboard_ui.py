from __future__ import annotations

from typing import Optional


def render_dashboard_html(*, title: str = "StarAgent Dashboard") -> str:
    """
    Lightweight local dashboard.

    Important:
    - This UI does not bypass API key checks. It calls the existing /v1/* APIs
      and requires the operator to provide the API key in-browser.
    - No server-side business logic duplication.
    """
    t = (title or "StarAgent Dashboard").strip()
    # Keep this self-contained (no external assets) for local operability.
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_escape_html(t)}</title>
    <style>
      :root {{
        --bg: #0b0f16;
        --panel: #121a26;
        --panel2: #0f1622;
        --text: #e6edf3;
        --muted: #9aa7b5;
        --accent: #6ee7ff;
        --danger: #ff5c7c;
        --ok: #56f1b5;
        --border: rgba(255,255,255,0.10);
        --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
      }}
      * {{ box-sizing: border-box; }}
      html, body {{ height: 100%; }}
      body {{
        margin: 0;
        font-family: var(--sans);
        background: radial-gradient(1200px 700px at 10% -10%, rgba(110,231,255,0.12), transparent 60%),
                    radial-gradient(900px 600px at 90% 10%, rgba(86,241,181,0.10), transparent 60%),
                    var(--bg);
        color: var(--text);
      }}
      a {{ color: var(--accent); text-decoration: none; }}
      a:hover {{ text-decoration: underline; }}
      header {{
        padding: 14px 18px;
        border-bottom: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        background: rgba(18,26,38,0.85);
        position: sticky;
        top: 0;
        backdrop-filter: blur(8px);
        z-index: 5;
      }}
      header h1 {{
        margin: 0;
        font-size: 15px;
        letter-spacing: 0.6px;
        font-weight: 650;
      }}
      header .right {{
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        justify-content: flex-end;
      }}
      .chip {{
        font-family: var(--mono);
        font-size: 12px;
        padding: 6px 8px;
        border: 1px solid var(--border);
        border-radius: 999px;
        color: var(--muted);
        background: rgba(0,0,0,0.15);
      }}
      main {{
        display: grid;
        grid-template-columns: 360px 1fr;
        gap: 14px;
        padding: 14px;
      }}
      @media (max-width: 980px) {{
        main {{ grid-template-columns: 1fr; }}
      }}
      .panel {{
        border: 1px solid var(--border);
        background: linear-gradient(180deg, rgba(18,26,38,0.75), rgba(15,22,34,0.7));
        border-radius: 12px;
        overflow: hidden;
      }}
      .panel .hd {{
        padding: 10px 12px;
        border-bottom: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }}
      .panel .hd h2 {{
        margin: 0;
        font-size: 13px;
        font-weight: 650;
        letter-spacing: 0.4px;
      }}
      .panel .bd {{
        padding: 12px;
      }}
      .row {{
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        align-items: center;
      }}
      label {{
        font-size: 12px;
        color: var(--muted);
      }}
      input, select, textarea {{
        background: var(--panel2);
        border: 1px solid var(--border);
        color: var(--text);
        border-radius: 10px;
        padding: 8px 10px;
        font-size: 13px;
        outline: none;
        width: 100%;
      }}
      textarea {{ min-height: 74px; resize: vertical; font-family: var(--sans); }}
      input.mono {{ font-family: var(--mono); font-size: 12px; }}
      .btn {{
        border: 1px solid var(--border);
        background: rgba(0,0,0,0.18);
        color: var(--text);
        border-radius: 10px;
        padding: 8px 10px;
        font-size: 13px;
        cursor: pointer;
      }}
      .btn:hover {{ border-color: rgba(110,231,255,0.45); }}
      .btn.primary {{ border-color: rgba(110,231,255,0.35); }}
      .btn.danger {{ border-color: rgba(255,92,124,0.35); color: #ffd1da; }}
      .btn.ok {{ border-color: rgba(86,241,181,0.30); color: #d3ffef; }}
      .btn.small {{ padding: 6px 8px; font-size: 12px; border-radius: 9px; }}
      .tabbtn {{
        font-family: var(--mono);
        font-size: 12px;
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: rgba(0,0,0,0.18);
        color: var(--muted);
        cursor: pointer;
      }}
      .tabbtn.active {{
        color: var(--text);
        border-color: rgba(110,231,255,0.55);
        background: rgba(110,231,255,0.10);
      }}
      .muted {{ color: var(--muted); }}
      .tasks {{
        display: flex;
        flex-direction: column;
        gap: 8px;
      }}
      .task {{
        padding: 10px 10px;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(0,0,0,0.14);
        cursor: pointer;
      }}
      .task:hover {{ border-color: rgba(110,231,255,0.30); }}
      .task.active {{ border-color: rgba(110,231,255,0.60); background: rgba(110,231,255,0.06); }}
      .task .top {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }}
      .task .id {{
        font-family: var(--mono);
        font-size: 11px;
        color: var(--muted);
      }}
      .task .status {{
        font-family: var(--mono);
        font-size: 11px;
        padding: 3px 7px;
        border-radius: 999px;
        border: 1px solid var(--border);
        color: var(--muted);
      }}
      .status.completed {{ color: var(--ok); border-color: rgba(86,241,181,0.35); }}
      .status.failed {{ color: var(--danger); border-color: rgba(255,92,124,0.40); }}
      .status.paused {{ color: #ffd47a; border-color: rgba(255,212,122,0.45); }}
      .status.partial {{ color: #c2d8ff; border-color: rgba(194,216,255,0.30); }}
      .status.running {{ color: var(--accent); border-color: rgba(110,231,255,0.40); }}
      .status.pending {{ color: #cbd5e1; border-color: rgba(203,213,225,0.25); }}
      .task .meta {{
        margin-top: 6px;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px;
        font-size: 12px;
        color: var(--muted);
      }}
      .kv {{
        display: grid;
        grid-template-columns: 150px 1fr;
        gap: 10px;
        font-size: 13px;
        align-items: baseline;
      }}
      .kv .k {{ color: var(--muted); font-family: var(--mono); font-size: 12px; }}
      .pre {{
        white-space: pre-wrap;
        background: rgba(0,0,0,0.20);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 10px;
        font-family: var(--mono);
        font-size: 12px;
        line-height: 1.45;
        max-height: 360px;
        overflow: auto;
      }}
      .grid2 {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
      }}
      @media (max-width: 980px) {{
        .grid2 {{ grid-template-columns: 1fr; }}
      }}
      .files {{
        display: flex;
        flex-direction: column;
        gap: 6px;
      }}
      .file {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 8px 10px;
        border: 1px solid var(--border);
        border-radius: 10px;
        background: rgba(0,0,0,0.12);
        cursor: pointer;
      }}
      .file:hover {{ border-color: rgba(110,231,255,0.30); }}
      .file .name {{ font-family: var(--mono); font-size: 12px; }}
      .file .tag {{ font-family: var(--mono); font-size: 11px; color: var(--muted); }}
      .warn {{
        color: #ffd47a;
      }}
      .err {{
        color: #ffd1da;
      }}

      .banner {{
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 10px;
        background: rgba(0,0,0,0.16);
      }}
      .banner.warn {{
        border-color: rgba(255,212,122,0.45);
        background: rgba(255,212,122,0.08);
      }}
      .banner .title {{
        font-family: var(--mono);
        font-size: 12px;
        color: #ffd47a;
        margin-bottom: 6px;
      }}
      .banner .body {{
        font-family: var(--mono);
        font-size: 12px;
        color: var(--text);
        white-space: pre-wrap;
      }}

      /* Chat UX */
      .chatWrap {{
        display: flex;
        flex-direction: column;
        gap: 10px;
        height: calc(100vh - 170px);
        min-height: 520px;
      }}
      .chatHistory {{
        flex: 1;
        overflow: auto;
        padding: 10px 8px;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(0,0,0,0.12);
      }}
      .bubble {{
        max-width: 900px;
        padding: 10px 12px;
        border-radius: 14px;
        border: 1px solid var(--border);
        margin-bottom: 10px;
      }}
      .bubble.user {{
        margin-left: auto;
        background: rgba(110,231,255,0.08);
        border-color: rgba(110,231,255,0.25);
      }}
      .bubble.assistant {{
        margin-right: auto;
        background: rgba(86,241,181,0.06);
        border-color: rgba(86,241,181,0.18);
      }}
      .bubble .meta {{
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 6px;
        font-family: var(--mono);
        font-size: 11px;
        color: var(--muted);
      }}
      .bubble .meta .left {{
        display: flex;
        gap: 8px;
        align-items: center;
      }}
      .bubble .content {{
        font-size: 13px;
        line-height: 1.55;
        color: var(--text);
        overflow-wrap: anywhere;
      }}
      .bubble .content pre {{
        white-space: pre-wrap;
        background: rgba(0,0,0,0.20);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 10px;
        font-family: var(--mono);
        font-size: 12px;
        line-height: 1.45;
        overflow: auto;
      }}
      .bubble .content code {{
        font-family: var(--mono);
        font-size: 12px;
        background: rgba(0,0,0,0.22);
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 8px;
        padding: 1px 6px;
      }}
      .bubble .actions {{
        margin-top: 8px;
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }}
      .composer {{
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 10px;
        background: rgba(0,0,0,0.12);
      }}
      .composer textarea {{
        width: 100%;
        min-height: 84px;
        background: rgba(0,0,0,0.18);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 10px;
        color: var(--text);
        font-family: var(--sans);
        font-size: 13px;
        line-height: 1.45;
        resize: vertical;
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>{_escape_html(t)}</h1>
      <div class="right">
        <span class="chip" id="chipBase">base: (loading)</span>
        <span class="chip" id="chipModel">model: (unknown)</span>
        <span class="chip" id="chipHealth">health: (unknown)</span>
      </div>
    </header>

    <main>
      <section class="panel" id="panelTasks">
        <div class="hd">
          <h2>Tasks</h2>
          <div class="row">
            <button class="btn small" id="btnRefresh">Refresh</button>
          </div>
        </div>
        <div class="bd">
          <div class="row" style="margin-bottom:10px;">
            <div style="flex:1; min-width: 200px;">
              <label>API key (stored locally in your browser)</label>
              <input id="apiKey" type="password" class="mono" placeholder="Bearer token (e.g. local-dev-key)" />
            </div>
            <div style="min-width:120px; align-self:flex-end;">
              <button class="btn primary" id="btnSaveKey">Save</button>
            </div>
          </div>

          <div class="row" style="margin-bottom:10px;">
            <div style="flex:1; min-width: 160px;">
              <label>Filter status</label>
              <select id="statusFilter">
                <option value="">(all)</option>
                <option value="pending">pending</option>
                <option value="running">running</option>
                <option value="paused">paused</option>
                <option value="partial">partial</option>
                <option value="completed">completed</option>
                <option value="failed">failed</option>
              </select>
            </div>
            <div style="flex:1; min-width: 160px;">
              <label>Filter project</label>
              <input id="projectFilter" class="mono" placeholder="(all)" />
            </div>
            <div style="flex:1; min-width: 160px;">
              <label>Limit</label>
              <input id="limit" type="number" value="30" />
            </div>
          </div>

          <div class="row" style="margin-bottom:10px;">
            <div style="flex:1; min-width: 160px;">
              <label>Filter task type</label>
              <select id="typeFilter">
                <option value="">(all)</option>
                <option value="agent">agent</option>
                <option value="research">research</option>
                <option value="repo_audit">repo_audit</option>
                <option value="issue_triage">issue_triage</option>
                <option value="writing">writing</option>
              </select>
            </div>
            <div style="flex:1; min-width: 160px;">
              <label>Filter pack/preset</label>
              <input id="packFilter" class="mono" placeholder="e.g. repo_onboarding / release_review" />
            </div>
          </div>

          <div id="tasks" class="tasks"></div>
          <div class="muted" id="tasksEmpty" style="display:none; margin-top:10px;">(no tasks found)</div>
          <div class="err" id="tasksErr" style="display:none; margin-top:10px;"></div>
        </div>
      </section>

      <section class="panel" id="panelDetail">
        <div class="hd">
          <div class="row">
            <button class="tabbtn active" id="tabDetail">Tasks</button>
            <button class="tabbtn" id="tabChat">Chat</button>
          </div>
          <div class="row" id="detailActions">
            <button class="btn small" id="btnContinue">Continue</button>
            <button class="btn small ok" id="btnApprove">Approve</button>
            <button class="btn small danger" id="btnReject">Reject</button>
            <button class="btn small primary" id="btnOpenPrimary">Open primary</button>
          </div>
        </div>
        <div class="bd">
          <div id="detailView">
            <div class="muted" id="noSelection">Select a task to view details.</div>

            <div id="detail" style="display:none;">
            <div id="approvalBox" class="banner warn" style="display:none; margin-bottom:12px;">
              <div class="title">Approval required</div>
              <div class="body" id="approvalText"></div>
            </div>
            <div class="kv">
              <div class="k">task_id</div><div class="v" id="dTaskId" style="font-family:var(--mono);"></div>
              <div class="k">status</div><div class="v" id="dStatus"></div>
              <div class="k">type</div><div class="v" id="dType" style="font-family:var(--mono);"></div>
              <div class="k">step</div><div class="v" id="dStep"></div>
              <div class="k">retry</div><div class="v" id="dRetry"></div>
              <div class="k">primary</div><div class="v" id="dPrimary"></div>
              <div class="k">verdict</div><div class="v" id="dVerdict"></div>
              <div class="k">pack/preset</div><div class="v" id="dPack"></div>
            </div>

            <div id="datasetBox" class="banner" style="display:none; margin-top:12px;">
              <div class="title">Dataset metrics</div>
              <div class="body" id="dDataset"></div>
            </div>

            <div class="grid2" style="margin-top:12px;">
              <div>
                <div class="muted" style="margin-bottom:6px;">Final summary (or last completed)</div>
                <div class="pre" id="dFinal"></div>
              </div>
              <div>
                <div class="muted" style="margin-bottom:6px;">Current step</div>
                <div class="pre" id="dCurrent"></div>
              </div>
            </div>

            <div class="grid2" style="margin-top:12px;">
              <div>
                <div class="row" style="justify-content: space-between; margin-bottom:6px;">
                  <div class="muted">Artifacts</div>
                  <div class="muted" id="dArtifactDir" style="font-family:var(--mono); font-size:11px;"></div>
                </div>
                <div class="files" id="files"></div>
              </div>
              <div>
                <div class="row" style="justify-content: space-between; margin-bottom:6px;">
                  <div class="muted">Logs (tail)</div>
                  <div>
                    <button class="btn small" id="btnRefreshDetail">Refresh detail</button>
                  </div>
                </div>
                <div class="pre" id="logs"></div>
              </div>
            </div>

            <div style="margin-top:12px;">
              <div class="row" style="justify-content: space-between; margin-bottom:6px;">
                <div class="muted">Artifact preview</div>
                <div class="row">
                  <label class="muted" style="margin-right:6px;">tail</label>
                  <input id="tailLines" type="number" value="200" style="width:90px;" />
                </div>
              </div>
              <div class="pre" id="preview">(select an artifact)</div>
            </div>
          </div>

          <div style="height: 14px;"></div>
          <div class="panel" style="border-radius: 12px;">
            <div class="hd"><h2>Launch Pack</h2></div>
            <div class="bd">
              <div class="row" style="margin-bottom:10px;">
                <div style="flex:1; min-width: 240px;">
                  <label>Pack</label>
                  <select id="packSelect"></select>
                </div>
                <div style="flex:1; min-width: 160px;">
                  <label>project_id</label>
                  <input id="packProject" class="mono" placeholder="default" />
                </div>
                <div style="flex:1; min-width: 160px;">
                  <label>conversation_id</label>
                  <input id="packConv" class="mono" placeholder="default" />
                </div>
              </div>
              <div class="grid2" style="margin-bottom:10px;">
                <div>
                  <label>path</label>
                  <input id="packPath" class="mono" placeholder="." />
                </div>
                <div>
                  <label>output_path (stateful packs)</label>
                  <input id="packOutput" class="mono" placeholder="sandbox_test/release_prep.md" />
                </div>
              </div>
              <div class="grid2" style="margin-bottom:10px;">
                <div>
                  <label>question</label>
                  <textarea id="packQuestion" placeholder="Optional"></textarea>
                </div>
                <div>
                  <label>issue</label>
                  <textarea id="packIssue" placeholder="Optional"></textarea>
                </div>
              </div>
              <div class="row" style="justify-content: space-between;">
                <div class="muted" id="packInfo"></div>
                <button class="btn primary" id="btnRunPack">Run pack</button>
              </div>
              <div class="pre" id="packResult" style="margin-top:10px; max-height: 220px; display:none;"></div>
            </div>
          </div>
          </div> <!-- /detailView -->

          <div id="chatView" style="display:none;">
            <div class="chatWrap">
              <div class="row" style="justify-content: space-between;">
                <div class="muted" style="font-family:var(--mono); font-size:12px;">
                  Chat runs tasks via the existing APIs. Use Ctrl+Enter to send.
                </div>
                <div class="row">
                  <button class="btn small" id="btnNewChat">New chat</button>
                  <button class="btn small" id="btnClearChat">Clear local</button>
                </div>
              </div>

              <div id="chatHistory" class="chatHistory"></div>

              <div class="composer">
                <div class="grid2" style="margin-bottom:10px;">
                  <div>
                    <label>path (optional)</label>
                    <input id="chatPath" class="mono" placeholder="e.g. /path/to/repo or /path/to/dataset_folder" />
                  </div>
                  <div>
                    <label>mode</label>
                    <select id="chatMode">
                      <option value="auto">auto (repo/dataset/docs)</option>
                      <option value="repo">repo</option>
                      <option value="dataset">dataset</option>
                      <option value="docs">docs</option>
                    </select>
                  </div>
                </div>

                <label>message</label>
                <textarea id="chatMsg" placeholder="Ask a question or request a task…"></textarea>
                <div class="row" style="justify-content: space-between; margin-top:10px;">
                  <div class="row">
                    <button class="btn ok" id="btnSendChat">Send</button>
                    <button class="btn" id="btnChatOpenTasks">Open Tasks tab</button>
                  </div>
                  <div class="muted" id="chatStatus" style="font-family:var(--mono); font-size:12px;"></div>
                </div>
              </div>
            </div>
          </div> <!-- /chatView -->
        </div>
      </section>
    </main>

    <script>
      const base = window.location.origin;
      document.getElementById("chipBase").textContent = "base: " + base;

      const LS_KEY = "staragent_api_key";
      const apiKeyEl = document.getElementById("apiKey");
      apiKeyEl.value = localStorage.getItem(LS_KEY) || "";
      document.getElementById("btnSaveKey").addEventListener("click", () => {{
        localStorage.setItem(LS_KEY, apiKeyEl.value.trim());
        refreshAll();
      }});

      // Optional URL params for demoability/onboarding links.
      // These are best-effort and never bypass API auth.
      // Supported:
      // - api_key: store in localStorage for this origin
      // - project/status/type/pack: set filters
      // - task_id: auto-open task detail
      // - artifact: auto-preview artifact in the selected task
      let autoTaskId = null;
      let autoArtifact = null;
      (function applyUrlParams() {{
        try {{
          const u = new URL(window.location.href);
          // Prefer hash fragment for API key so it is not sent to the server (and won't show up in access logs).
          // Example: /dashboard#api_key=local-dev-key
          const hashParams = new URLSearchParams(String(u.hash || "").replace(/^#/, ""));
          const apiKey = (hashParams.get("api_key") || hashParams.get("key") || "").trim();
          const project = (u.searchParams.get("project") || "").trim();
          const status = (u.searchParams.get("status") || "").trim();
          const type = (u.searchParams.get("type") || "").trim();
          const pack = (u.searchParams.get("pack") || "").trim();
          autoTaskId = (u.searchParams.get("task_id") || u.searchParams.get("task") || "").trim() || null;
          autoArtifact = (u.searchParams.get("artifact") || "").trim() || null;

          let touched = false;
          if (apiKey) {{
            localStorage.setItem(LS_KEY, apiKey);
            apiKeyEl.value = apiKey;
            touched = true;
          }}
          if (project) {{
            document.getElementById("projectFilter").value = project;
            touched = true;
          }}
          if (status) {{
            document.getElementById("statusFilter").value = status;
            touched = true;
          }}
          if (type) {{
            document.getElementById("typeFilter").value = type;
            touched = true;
          }}
          if (pack) {{
            document.getElementById("packFilter").value = pack;
            touched = true;
          }}
          if (autoTaskId || autoArtifact) touched = true;

          // Remove fragment/query helpers to avoid leaking ids via copy/paste history.
          if (touched && window.history && window.history.replaceState) {{
            const clean = u.pathname; // keep it simple; no need to preserve params for operator UI
            window.history.replaceState({{}}, "", clean);
          }}
        }} catch (e) {{
          // Ignore.
        }}
      }})();

      function authHeader() {{
        const v = (localStorage.getItem(LS_KEY) || "").trim();
        if (!v) return {{}};
        // Accept either raw token or full "Bearer ..." string.
        const token = v.toLowerCase().startsWith("bearer ") ? v.slice(7).trim() : v;
        return {{ "Authorization": "Bearer " + token }};
      }}

      async function apiFetch(path, opts={{}}) {{
        const headers = Object.assign({{"Content-Type":"application/json"}}, authHeader(), (opts.headers||{{}}));
        const res = await fetch(base + path, Object.assign({{}}, opts, {{ headers }}));
        if (!res.ok) {{
          const text = await res.text();
          throw new Error(res.status + " " + res.statusText + ": " + text);
        }}
        const ct = res.headers.get("content-type") || "";
        if (ct.includes("application/json")) return await res.json();
        return await res.text();
      }}

      function fmtStatus(s) {{
        s = (s||"").toLowerCase();
        return s || "unknown";
      }}

      function setText(id, v) {{
        const el = document.getElementById(id);
        if (el) el.textContent = (v === null || v === undefined) ? "" : String(v);
      }}

      function el(tag, cls, txt) {{
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (txt !== undefined) e.textContent = txt;
        return e;
      }}

      let selectedTaskId = null;
      let selectedPrimaryName = null;

      async function refreshHeader() {{
        try {{
          const h = await apiFetch("/health");
          document.getElementById("chipHealth").textContent = "health: ok";
          document.getElementById("chipModel").textContent = "model: " + (h.default_model || "(unknown)");
        }} catch (e) {{
          document.getElementById("chipHealth").textContent = "health: error";
        }}
      }}

      async function refreshTasks() {{
        const tasksEl = document.getElementById("tasks");
        const emptyEl = document.getElementById("tasksEmpty");
        const errEl = document.getElementById("tasksErr");
        errEl.style.display = "none";
        emptyEl.style.display = "none";
        tasksEl.innerHTML = "";

        const status = document.getElementById("statusFilter").value;
        const project = (document.getElementById("projectFilter").value || "").trim();
        const limit = parseInt(document.getElementById("limit").value || "30", 10);
        let qs = `?limit=${{Math.max(1, Math.min(limit, 200))}}&offset=0`;
        if (status) qs += `&status=${{encodeURIComponent(status)}}`;
        if (project) qs += `&project_id=${{encodeURIComponent(project)}}`;
        try {{
          const out = await apiFetch("/v1/tasks" + qs);
          let tasks = out.tasks || [];

          // Client-side filters (API is kept stable; no new query params needed).
          const typeFilter = (document.getElementById("typeFilter").value || "").trim().toLowerCase();
          const packFilter = (document.getElementById("packFilter").value || "").trim().toLowerCase();
          if (typeFilter) {{
            tasks = tasks.filter(t => String(t.task_type || "").toLowerCase() === typeFilter);
          }}
          if (packFilter) {{
            tasks = tasks.filter(t => {{
              const aj = t.artifacts_json || {{}};
              const pack = String(aj.pack_name || "").toLowerCase();
              const preset = String(aj.preset || aj.pack_preset || "").toLowerCase();
              const conv = String(t.conversation_id || "").toLowerCase();
              return pack.includes(packFilter) || preset.includes(packFilter) || conv.includes(packFilter);
            }});
          }}
          if (!tasks.length) {{
            emptyEl.style.display = "block";
            return;
          }}
          for (const t of tasks) {{
            const item = el("div", "task" + (t.task_id === selectedTaskId ? " active" : ""), "");
            const top = el("div", "top", "");
            const id = el("div", "id", (t.task_id || "").slice(0, 8) + "…" );
            const st = el("div", "status " + fmtStatus(t.status), fmtStatus(t.status));
            top.appendChild(id);
            top.appendChild(st);
            item.appendChild(top);

            const meta = el("div", "meta", "");
            meta.appendChild(el("div", "", "type: " + (t.task_type || "-")));
            meta.appendChild(el("div", "", "step: " + (t.current_step_index ?? "-") + "/" + (t.max_steps ?? "-")));
            meta.appendChild(el("div", "", "retry: " + (t.retry_count ?? 0)));
            const aj = t.artifacts_json || {{}};
            const pack = aj.pack_name ? ("pack: " + aj.pack_name) : (aj.preset ? ("preset: " + aj.preset) : ("proj: " + (t.project_id || "-")));
            meta.appendChild(el("div", "", pack));
            item.appendChild(meta);

            item.addEventListener("click", () => {{
              selectedTaskId = t.task_id;
              refreshTasks();
              loadTaskDetail(t.task_id);
            }});
            tasksEl.appendChild(item);
          }}
        }} catch (e) {{
          errEl.textContent = "Error loading tasks: " + e.message;
          errEl.style.display = "block";
        }}
      }}

      async function loadTaskDetail(taskId) {{
        document.getElementById("noSelection").style.display = "none";
        document.getElementById("detail").style.display = "block";
        setText("preview", "(select an artifact)");
        setText("logs", "(loading)");
        document.getElementById("files").innerHTML = "";
        setText("dTaskId", taskId);

        try {{
          const [inspect, summary, artifacts, logs] = await Promise.all([
            apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/inspect`),
            apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/summary`),
            apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/artifacts`),
            apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/logs?tail_steps=30`),
          ]);

          const tr = (inspect.task || {{}});
          const prog = (inspect.progress || {{}});
          const cur = (prog.current_step || {{}});
          const meta = (summary.task_meta || inspect.task_meta || {{}});
          const primary = (meta.primary_artifact || inspect.primary_artifact || artifacts.primary_artifact || {{}});
          selectedPrimaryName = primary.name || null;

          setText("dStatus", tr.status || "");
          setText("dType", tr.task_type || "");
          setText("dStep", (tr.current_step_index ?? "-") + "/" + (tr.max_steps ?? "-"));
          setText("dRetry", tr.retry_count ?? 0);
          setText("dVerdict", tr.final_verdict || "");
          setText("dPrimary", primary.name ? `${{primary.name}} (${{primary.exists ? "ok" : "missing"}})` : "-");
          setText("dArtifactDir", artifacts.artifact_dir || "");

          const aj = tr.artifacts_json || {{}};
          const packName = aj.pack_name || "";
          const presetName = aj.preset || aj.pack_preset || "";
          const packDisp = packName ? ("pack: " + packName) : (presetName ? ("preset: " + presetName) : "-");
          setText("dPack", packDisp);

          const final = (summary.task || {{}}).final_summary || "";
          setText("dFinal", final || "(none)");
          const curTxt = [
            `#${{cur.step_index ?? "-"}} ${{cur.step_type || ""}} [${{cur.status || ""}}]`,
            (cur.instruction || ""),
          ].filter(Boolean).join("\\n\\n");
          setText("dCurrent", curTxt || "(none)");

          // dataset metrics (optional; shown for dataset-mode research tasks)
          const dmBox = document.getElementById("datasetBox");
          const dmText = document.getElementById("dDataset");
          dmBox.style.display = "none";
          dmText.textContent = "";
          const dm = (meta.dataset_meta || summary.dataset_meta || inspect.dataset_meta || null);
          if (dm && typeof dm === "object") {{
            const lines = Array.isArray(dm.display_lines) ? dm.display_lines : [];
            if (lines.length) {{
              dmText.textContent = lines.join("\\n");
              dmBox.style.display = "block";
            }}
          }}

          // logs
          const logsArr = logs.logs || [];
          const logLines = logsArr.map(x => {{
            const head = `#${{x.step_index}} ${{x.step_type}} [${{x.status}}] (attempt=${{x.attempt_count}})`;
            const instr = x.instruction ? ("instr: " + x.instruction) : "";
            const out = x.output_summary ? ("out: " + String(x.output_summary).slice(0, 700)) : "";
            return [head, instr, out].filter(Boolean).join("\\n");
          }}).join("\\n\\n");
          setText("logs", logLines || "(no logs)");

          // artifacts list
          const filesEl = document.getElementById("files");
          filesEl.innerHTML = "";
          const files = artifacts.files || [];
          if (!files.length) {{
            filesEl.appendChild(el("div", "muted", "(no artifacts)"));
          }} else {{
            for (const f of files) {{
              const row = el("div", "file", "");
              const left = el("div", "", "");
              const nm = el("div", "name", (f.is_primary ? "★ " : "") + f.name);
              left.appendChild(nm);
              row.appendChild(left);
              row.appendChild(el("div", "tag", (f.type || "") + "  " + (f.size_bytes ? (f.size_bytes + "B") : "")));
              row.addEventListener("click", () => previewArtifact(taskId, f.name, f.type));
              filesEl.appendChild(row);
            }}
          }}

          // approval clarity
          const apprBox = document.getElementById("approvalBox");
          const apprText = document.getElementById("approvalText");
          apprBox.style.display = "none";
          apprText.textContent = "";
          const approval = (meta.approval || null);
          const pending = (aj && aj.pending_approval) ? aj.pending_approval : null;
          if (approval && approval.required) {{
            const lines = Array.isArray(approval.display_lines) ? approval.display_lines : [];
            apprText.textContent = lines.join("\\n");
            apprBox.style.display = "block";
          }} else if (pending && pending.tool_call) {{
            // Back-compat fallback: older servers store approval in artifacts_json.
            const tc = pending.tool_call;
            const fn = (tc.function || {{}}).name || "unknown";
            apprText.textContent = "action: " + fn;
            apprBox.style.display = "block";
          }}

          // Action buttons state
          const st = String(tr.status || "").toLowerCase();
          const btnC = document.getElementById("btnContinue");
          const btnA = document.getElementById("btnApprove");
          const btnR = document.getElementById("btnReject");
          // Allow starting newly-created tasks as well.
          btnC.disabled = !(st === "pending" || st === "partial" || st === "running");
          const canApprove = (approval && approval.required) || (st === "paused" && pending);
          btnA.disabled = !(st === "paused" && canApprove);
          btnR.disabled = !(st === "paused" && canApprove);
        }} catch (e) {{
          setText("dFinal", "Error loading task detail: " + e.message);
        }}
      }}

      async function previewArtifact(taskId, name, type) {{
        const tail = parseInt(document.getElementById("tailLines").value || "200", 10);
        let fmt = "text";
        if ((type || "").toLowerCase() === "json" || (name || "").toLowerCase().endsWith(".json")) fmt = "json";
        try {{
          const out = await apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/artifacts/${{encodeURIComponent(name)}}?format=${{encodeURIComponent(fmt)}}&tail_lines=${{encodeURIComponent(String(Math.max(0, Math.min(tail, 2000))))}}`);
          const extra = out.truncated ? "\\n\\n(warn) truncated" : "";
          setText("preview", out.content + extra);
        }} catch (e) {{
          setText("preview", "Error previewing artifact: " + e.message);
        }}
      }}

      async function taskAction(action) {{
        if (!selectedTaskId) return;
        const body = {{
          action,
          max_step_advances: 6,
          max_duration_s: 50.0,
        }};
        if (action === "reject") {{
          const reason = prompt("Reject reason?", "rejected");
          body.reason = reason || "rejected";
        }}
        try {{
          await apiFetch(`/v1/tasks/${{encodeURIComponent(selectedTaskId)}}/continue`, {{
            method: "POST",
            body: JSON.stringify(body),
          }});
          await refreshTasks();
          await loadTaskDetail(selectedTaskId);
        }} catch (e) {{
          alert("Action failed: " + e.message);
        }}
      }}

      document.getElementById("btnContinue").addEventListener("click", () => taskAction("continue"));
      document.getElementById("btnApprove").addEventListener("click", () => taskAction("approve"));
      document.getElementById("btnReject").addEventListener("click", () => taskAction("reject"));
      document.getElementById("btnOpenPrimary").addEventListener("click", () => {{
        if (!selectedTaskId || !selectedPrimaryName) return;
        previewArtifact(selectedTaskId, selectedPrimaryName, (selectedPrimaryName || "").toLowerCase().endsWith(".json") ? "json" : "markdown");
      }});
      document.getElementById("btnRefresh").addEventListener("click", refreshAll);
      document.getElementById("btnRefreshDetail").addEventListener("click", () => selectedTaskId && loadTaskDetail(selectedTaskId));
      document.getElementById("statusFilter").addEventListener("change", refreshTasks);
      document.getElementById("projectFilter").addEventListener("change", () => {{
        refreshTasks();
        if (activeTab === "chat") renderChat();
      }});
      document.getElementById("typeFilter").addEventListener("change", refreshTasks);
      document.getElementById("packFilter").addEventListener("change", refreshTasks);

      // Packs
      async function loadPacks() {{
        const sel = document.getElementById("packSelect");
        const info = document.getElementById("packInfo");
        sel.innerHTML = "";
        info.textContent = "";
        try {{
          const out = await apiFetch("/v1/presets/packs");
          const packs = out.packs || [];
          for (const p of packs) {{
            const opt = document.createElement("option");
            opt.value = p.name;
            opt.textContent = p.name;
            opt.dataset.desc = p.description || "";
            opt.dataset.ro = p.read_only ? "true" : "false";
            opt.dataset.appr = p.may_require_approval ? "true" : "false";
            opt.dataset.primary = p.primary_artifact || "";
            sel.appendChild(opt);
          }}
          if (packs.length) {{
            sel.value = packs[0].name;
            updatePackInfo();
          }}
        }} catch (e) {{
          info.textContent = "Error loading packs: " + e.message;
        }}
      }}

      function updatePackInfo() {{
        const sel = document.getElementById("packSelect");
        const opt = sel.options[sel.selectedIndex];
        const info = document.getElementById("packInfo");
        if (!opt) return;
        const ro = opt.dataset.ro === "true" ? "read-only" : "stateful";
        const roLabel = ro === "read-only" ? "RO" : "STATEFUL";
        const appr = opt.dataset.appr === "true" ? "may require approval" : "no approval expected";
        const primary = opt.dataset.primary || "-";
        info.textContent = `${{opt.dataset.desc}}  (${{roLabel}}, primary=${{primary}}, ${{appr}})`;
      }}

      document.getElementById("packSelect").addEventListener("change", updatePackInfo);

      document.getElementById("btnRunPack").addEventListener("click", async () => {{
        const packName = document.getElementById("packSelect").value;
        const project = document.getElementById("packProject").value.trim() || "default";
        const conv = document.getElementById("packConv").value.trim() || "default";
        const path = document.getElementById("packPath").value.trim() || ".";
        const question = document.getElementById("packQuestion").value.trim() || null;
        const issue = document.getElementById("packIssue").value.trim() || null;
        const output_path = document.getElementById("packOutput").value.trim() || null;

        const body = {{
          project_id: project,
          conversation_id: conv,
          path,
          question,
          issue,
          output_path,
          run_now: true,
        }};
        const pre = document.getElementById("packResult");
        pre.style.display = "block";
        pre.textContent = "(running...)";
        try {{
          const out = await apiFetch(`/v1/presets/packs/${{encodeURIComponent(packName)}}/run`, {{
            method: "POST",
            body: JSON.stringify(body),
          }});
          pre.textContent = JSON.stringify(out, null, 2);
          // Best effort: auto-select last task.
          const runs = out.runs || [];
          for (let i=runs.length-1; i>=0; i--) {{
            const tid = (runs[i].task || {{}}).task_id;
            if (tid) {{
              selectedTaskId = tid;
              break;
            }}
          }}
          await refreshTasks();
          if (selectedTaskId) await loadTaskDetail(selectedTaskId);
        }} catch (e) {{
          pre.textContent = "Error: " + e.message;
        }}
      }});

      // ============================================================
      // Chat tab (task-backed, ChatGPT-like)
      // ============================================================

      let activeTab = "detail";

      function syncChatInputs() {{
        const pathEl = document.getElementById("chatPath");
        const modeEl = document.getElementById("chatMode");
        if (!pathEl || !modeEl) return;
        const lastPath = (localStorage.getItem(CHAT_LAST_PATH_KEY) || "").trim();
        const lastMode = (localStorage.getItem(CHAT_LAST_MODE_KEY) || "auto").trim();
        if (!String(pathEl.value || "").trim() && lastPath) pathEl.value = lastPath;
        if (lastMode) modeEl.value = lastMode;
      }}

      function showTab(name) {{
        activeTab = (name === "chat") ? "chat" : "detail";
        const detailView = document.getElementById("detailView");
        const chatView = document.getElementById("chatView");
        const actions = document.getElementById("detailActions");
        const td = document.getElementById("tabDetail");
        const tc = document.getElementById("tabChat");
        if (activeTab === "chat") {{
          detailView.style.display = "none";
          chatView.style.display = "block";
          actions.style.display = "none";
          td.classList.remove("active");
          tc.classList.add("active");
          syncChatInputs();
          renderChat();
        }} else {{
          detailView.style.display = "block";
          chatView.style.display = "none";
          actions.style.display = "flex";
          td.classList.add("active");
          tc.classList.remove("active");
        }}
      }}

      document.getElementById("tabDetail").addEventListener("click", () => showTab("detail"));
      document.getElementById("tabChat").addEventListener("click", () => showTab("chat"));
      document.getElementById("btnChatOpenTasks").addEventListener("click", () => showTab("detail"));

      function _escapeHtmlJs(s) {{
        s = String(s || "");
        return s
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }}

      function _renderMarkdown(md) {{
        const raw = String(md || "");
        const blocks = [];
        const withTokens = raw.replace(/```([\\s\\S]*?)```/g, (m, p1) => {{
          blocks.push(String(p1 || ""));
          return `@@CODEBLOCK${{blocks.length - 1}}@@`;
        }});
        let html = _escapeHtmlJs(withTokens);
        html = html.replace(/@@CODEBLOCK(\\d+)@@/g, (m, idx) => {{
          const i = parseInt(idx || "0", 10);
          const body = _escapeHtmlJs(blocks[i] || "");
          return `<pre><code>${{body}}</code></pre>`;
        }});
        html = html.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, (m, t, u) => {{
          const text = _escapeHtmlJs(t);
          const url = _escapeHtmlJs(u);
          return `<a href="${{url}}" target="_blank" rel="noreferrer">${{text}}</a>`;
        }});
        html = html.replace(/^###\\s+(.+)$/gm, "<h3>$1</h3>");
        html = html.replace(/^##\\s+(.+)$/gm, "<h2>$1</h2>");
        html = html.replace(/^#\\s+(.+)$/gm, "<h1>$1</h1>");
        html = html.replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
        html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
        const lines = html.split(/\\n/);
        let out = [];
        let inList = false;
        for (const ln of lines) {{
          if (ln.startsWith("- ")) {{
            if (!inList) {{
              out.push("<ul>");
              inList = true;
            }}
            out.push("<li>" + ln.slice(2) + "</li>");
          }} else {{
            if (inList) {{
              out.push("</ul>");
              inList = false;
            }}
            out.push(ln);
          }}
        }}
        if (inList) out.push("</ul>");
        return out.join("\\n").replace(/\\n\\n+/g, "<br><br>").replace(/\\n/g, "<br>");
      }}

      const CHAT_SESSION_KEY = "staragent_dash_chat_session";
      const CHAT_HISTORY_PREFIX = "staragent_dash_chat_history:";
      const CHAT_LAST_PATH_KEY = "staragent_dash_chat_last_path";
      const CHAT_LAST_MODE_KEY = "staragent_dash_chat_last_mode";

      function chatProjectId() {{
        const v = (document.getElementById("projectFilter").value || "").trim();
        return v || "default";
      }}

      function ensureChatSession() {{
        let sid = (localStorage.getItem(CHAT_SESSION_KEY) || "").trim();
        if (!sid) {{
          sid = "dash-" + Math.random().toString(36).slice(2, 10);
          localStorage.setItem(CHAT_SESSION_KEY, sid);
        }}
        return sid;
      }}

      function chatConversationId() {{
        return ensureChatSession();
      }}

      function chatStorageKey() {{
        return CHAT_HISTORY_PREFIX + chatProjectId() + ":" + chatConversationId();
      }}

      function loadChatHistory() {{
        try {{
          const raw = localStorage.getItem(chatStorageKey()) || "[]";
          const arr = JSON.parse(raw);
          return Array.isArray(arr) ? arr : [];
        }} catch (e) {{
          return [];
        }}
      }}

      function saveChatHistory(arr) {{
        try {{
          localStorage.setItem(chatStorageKey(), JSON.stringify(arr));
        }} catch (e) {{}}
      }}

      function pushChatMessage(msg) {{
        const hist = loadChatHistory();
        hist.push(msg);
        saveChatHistory(hist);
        renderChat();
      }}

      function updateLastAssistantMessage(patch) {{
        const hist = loadChatHistory();
        for (let i = hist.length - 1; i >= 0; i--) {{
          if (hist[i] && hist[i].role === "assistant") {{
            hist[i] = Object.assign({{}}, hist[i], patch || {{}});
            break;
          }}
        }}
        saveChatHistory(hist);
        renderChat();
      }}

      function clearChatHistory() {{
        saveChatHistory([]);
        renderChat();
      }}

      function newChatSession() {{
        const sid = "dash-" + Math.random().toString(36).slice(2, 10);
        localStorage.setItem(CHAT_SESSION_KEY, sid);
        clearChatHistory();
      }}

      function openTaskInDetail(taskId) {{
        if (!taskId) return;
        selectedTaskId = taskId;
        refreshTasks();
        loadTaskDetail(taskId);
        showTab("detail");
      }}

      function renderChat() {{
        const elHist = document.getElementById("chatHistory");
        if (!elHist) return;
        const hist = loadChatHistory();
        elHist.innerHTML = "";
        if (!hist.length) {{
          const d = document.createElement("div");
          d.className = "muted";
          d.textContent = "(no messages yet)";
          elHist.appendChild(d);
          return;
        }}
        for (const m of hist) {{
          const b = document.createElement("div");
          const role = (m.role || "assistant") === "user" ? "user" : "assistant";
          b.className = "bubble " + role;

          const meta = document.createElement("div");
          meta.className = "meta";
          const left = document.createElement("div");
          left.className = "left";
          const who = document.createElement("div");
          who.textContent = role === "user" ? "user" : "assistant";
          left.appendChild(who);
          if (m.mode) {{
            const chip = document.createElement("div");
            chip.className = "chip";
            chip.textContent = "mode: " + m.mode;
            left.appendChild(chip);
          }}
          if (m.path) {{
            const chip2 = document.createElement("div");
            chip2.className = "chip";
            chip2.textContent = "path: " + String(m.path).slice(0, 48);
            left.appendChild(chip2);
          }}
          meta.appendChild(left);
          const ts = document.createElement("div");
          ts.textContent = m.ts ? new Date(m.ts).toLocaleString() : "";
          meta.appendChild(ts);
          b.appendChild(meta);

          const content = document.createElement("div");
          content.className = "content";
          if (role === "assistant") {{
            content.innerHTML = _renderMarkdown(m.content || "");
          }} else {{
            content.innerHTML = _escapeHtmlJs(m.content || "").replace(/\\n/g, "<br>");
          }}
          b.appendChild(content);

          const actions = document.createElement("div");
          actions.className = "actions";
          if (m.task_id) {{
            const btn = document.createElement("button");
            btn.className = "btn small primary";
            btn.textContent = "Open task artifacts";
            btn.addEventListener("click", () => openTaskInDetail(m.task_id));
            actions.appendChild(btn);
          }}
          if (m.approval && m.approval.required && m.task_id) {{
            const btnA = document.createElement("button");
            btnA.className = "btn small ok";
            btnA.textContent = "Approve";
            btnA.addEventListener("click", () => chatApprove(m.task_id));
            const btnR = document.createElement("button");
            btnR.className = "btn small danger";
            btnR.textContent = "Reject";
            btnR.addEventListener("click", () => chatReject(m.task_id));
            actions.appendChild(btnA);
            actions.appendChild(btnR);
          }}
          if (actions.childNodes.length) b.appendChild(actions);

          elHist.appendChild(b);
        }}
        elHist.scrollTop = elHist.scrollHeight;
      }}

      async function classifyPath(path) {{
        const body = {{ path }};
        return await apiFetch("/v1/intake/classify", {{ method: "POST", body: JSON.stringify(body) }});
      }}

      async function waitTask(taskId, *, loops=10, stepAdvances=6, durationS=35.0) {{
        let last = null;
        for (let i=1; i<=loops; i++) {{
          setText("chatStatus", "working... (loop " + i + "/" + loops + ")");
          const out = await apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/continue`, {{
            method: "POST",
            body: JSON.stringify({{ action: "continue", max_step_advances: stepAdvances, max_duration_s: durationS }}),
          }});
          last = out;
          const task = (out.task || {{}});
          const st = String(task.status || "").toLowerCase();
          const steps = out.steps || [];
          let completed = 0;
          for (const s of steps) if (s && s.status === "completed") completed++;
          const cur = (task.current_step_index !== null && task.current_step_index !== undefined) ? ("#" + task.current_step_index) : "";
          setText("chatStatus", "status=" + st + " steps=" + completed + "/" + steps.length + " " + cur);
          if (st === "completed" || st === "failed" || st === "paused") break;
          if (out.action_required && String(out.action_required.type || "").toLowerCase() === "approval") break;
        }}
        return last;
      }}

      async function fetchPrimaryArtifactText(taskId) {{
        const summary = await apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/summary`);
        const meta = summary.task_meta || {{}};
        const primary = (meta.primary_artifact || summary.primary_artifact || {{}}) || {{}};
        const name = primary.name || null;
        if (!name) {{
          return {{ summary, primary_name: null, content: (summary.task || {{}}).final_summary || "" }};
        }}
        const fmt = String(name).toLowerCase().endsWith(".json") ? "json" : "text";
        const prev = await apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/artifacts/${{encodeURIComponent(name)}}?format=${{encodeURIComponent(fmt)}}&tail_lines=0&max_bytes=200000`);
        return {{ summary, primary_name: name, content: prev.content || "" }};
      }}

      async function runChatSubmit() {{
        const msgEl = document.getElementById("chatMsg");
        const pathEl = document.getElementById("chatPath");
        const modeEl = document.getElementById("chatMode");
        const text = (msgEl.value || "").trim();
        const path = (pathEl.value || "").trim();
        const mode = (modeEl.value || "auto").trim();
        if (!text) return;

        localStorage.setItem(CHAT_LAST_PATH_KEY, path);
        localStorage.setItem(CHAT_LAST_MODE_KEY, mode);

        const ts = Date.now();
        pushChatMessage({{ role: "user", content: text, path, mode, ts }});
        pushChatMessage({{ role: "assistant", content: "_Working..._", path, mode, ts: Date.now(), status: "running" }});
        msgEl.value = "";

        const project_id = chatProjectId();
        const conversation_id = chatConversationId();

        try {{
          if (!path) {{
            setText("chatStatus", "sending chat...");
            const payload = {{
              model: null,
              messages: [{{ role: "user", content: text }}],
              temperature: 0.2,
              stream: false,
              project_id,
              conversation_id,
            }};
            const out = await apiFetch("/v1/chat/completions", {{ method: "POST", body: JSON.stringify(payload) }});
            const content = (((out.choices || [])[0] || {{}}).message || {{}}).content || "";
            updateLastAssistantMessage({{ content: String(content || ""), status: "completed" }});
            setText("chatStatus", "ok");
            return;
          }}

          let eff = mode;
          if (mode === "auto") {{
            setText("chatStatus", "classifying input...");
            const intake = await classifyPath(path);
            const it = String(intake.input_type || "");
            if (it === "repo") eff = "repo";
            else if (it === "json_dataset") eff = "dataset";
            else eff = "docs";
          }}

          setText("chatStatus", "creating task...");
          let created = null;
          if (eff === "repo") {{
            created = await apiFetch("/v1/repo_audit/run", {{
              method: "POST",
              body: JSON.stringify({{
                project_id,
                conversation_id,
                path,
                question: text,
                max_steps: 25,
                max_retries: 1,
                run_now: true,
              }}),
            }});
          }} else if (eff === "dataset") {{
            created = await apiFetch("/v1/presets/dataset_theme_report/run", {{
              method: "POST",
              body: JSON.stringify({{
                project_id,
                conversation_id,
                path,
                question: text,
                run_now: true,
              }}),
            }});
          }} else {{
            created = await apiFetch("/v1/research/run", {{
              method: "POST",
              body: JSON.stringify({{
                project_id,
                conversation_id,
                path,
                question: text,
                mode: "research",
                max_steps: 60,
                max_retries: 1,
                run_now: true,
              }}),
            }});
          }}
          const taskId = (created.task || {{}}).task_id || created.task_id;
          if (!taskId) throw new Error("missing task_id in response");

          await waitTask(taskId, {{ loops: 10, stepAdvances: 6, durationS: 35.0 }});
          await refreshTasks();

          const summary = await apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/summary`);
          const meta = summary.task_meta || {{}};
          const st = String((summary.task || {{}}).status || "").toLowerCase();
          const approval = meta.approval || null;

          if (st === "paused" || (approval && approval.required)) {{
            const lines = (approval && Array.isArray(approval.display_lines)) ? approval.display_lines : ["approval required"];
            updateLastAssistantMessage({{
              content: "Approval required:\\n\\n" + lines.map(x => "- " + x).join("\\n"),
              status: "paused",
              task_id: taskId,
              approval: approval || {{ required: true }},
            }});
            setText("chatStatus", "paused (approval required)");
            return;
          }}

          const pr = await fetchPrimaryArtifactText(taskId);
          updateLastAssistantMessage({{
            content: pr.content || (summary.task || {{}}).final_summary || "",
            status: st || "completed",
            task_id: taskId,
            primary_artifact: pr.primary_name || null,
          }});
          setText("chatStatus", st || "ok");
        }} catch (e) {{
          updateLastAssistantMessage({{ content: "Error: " + e.message, status: "error" }});
          setText("chatStatus", "error");
        }}
      }}

      async function chatApprove(taskId) {{
        if (!taskId) return;
        try {{
          setText("chatStatus", "approving...");
          await apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/continue`, {{
            method: "POST",
            body: JSON.stringify({{ action: "approve", max_step_advances: 3, max_duration_s: 20.0 }}),
          }});
          await waitTask(taskId, {{ loops: 10, stepAdvances: 6, durationS: 35.0 }});
          await refreshTasks();
          const pr = await fetchPrimaryArtifactText(taskId);
          updateLastAssistantMessage({{
            content: pr.content || "",
            status: "completed",
            task_id: taskId,
            approval: null,
          }});
          setText("chatStatus", "approved");
        }} catch (e) {{
          updateLastAssistantMessage({{ content: "Approve error: " + e.message, status: "error", task_id: taskId }});
          setText("chatStatus", "error");
        }}
      }}

      async function chatReject(taskId) {{
        if (!taskId) return;
        try {{
          setText("chatStatus", "rejecting...");
          await apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/continue`, {{
            method: "POST",
            body: JSON.stringify({{ action: "reject", reason: "rejected", max_step_advances: 1, max_duration_s: 10.0 }}),
          }});
          await refreshTasks();
          updateLastAssistantMessage({{ content: "Rejected.", status: "paused", task_id: taskId, approval: null }});
          setText("chatStatus", "rejected");
        }} catch (e) {{
          updateLastAssistantMessage({{ content: "Reject error: " + e.message, status: "error", task_id: taskId }});
          setText("chatStatus", "error");
        }}
      }}

      document.getElementById("btnSendChat").addEventListener("click", runChatSubmit);
      document.getElementById("btnNewChat").addEventListener("click", () => {{
        newChatSession();
        setText("chatStatus", "new chat");
      }});
      document.getElementById("btnClearChat").addEventListener("click", () => {{
        clearChatHistory();
        setText("chatStatus", "cleared");
      }});
      document.getElementById("chatMsg").addEventListener("keydown", (ev) => {{
        if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {{
          ev.preventDefault();
          runChatSubmit();
        }}
      }});

      async function refreshAll() {{
        await refreshHeader();
        await loadPacks();
        await refreshTasks();
        if (selectedTaskId) {{
          await loadTaskDetail(selectedTaskId);
        }}
        if (autoTaskId) {{
          selectedTaskId = autoTaskId;
          await loadTaskDetail(autoTaskId);
          if (autoArtifact) {{
            // Let detail populate and then preview.
            setTimeout(() => {{
              previewArtifact(autoTaskId, autoArtifact, (autoArtifact || "").toLowerCase().endsWith(".json") ? "json" : "markdown");
              const p = document.getElementById("preview");
              if (p && p.scrollIntoView) {{
                p.scrollIntoView({{ block: "start" }});
              }}
            }}, 650);
          }}
        }}
      }}

      refreshAll();
    </script>
  </body>
</html>
"""


def _escape_html(s: Optional[str]) -> str:
    s = s or ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
