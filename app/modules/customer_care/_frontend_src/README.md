# Agent Inbox (React)

Real-time agent inbox SPA for the Customer Care Automation backend.

## Stack
- **React 18 + TypeScript + Vite** — fast dev, typed everywhere
- **Tailwind CSS** — utility styling, Bangla fonts via Noto Sans Bengali
- **SWR** — request cache + revalidation for `/api/conversations`, messages, reports
- **Native EventSource** — `/api/inbox/stream` for live updates
- **React Router v6** — `/login` → `/change-password` → `/inbox`

## Features
- JWT login + auto-refresh + force first-password-change
- 3-pane layout: conversation list · conversation detail · live reports
- Live SSE updates with auto-reconnect (exponential backoff to 30s)
- Send agent reply (Enter to send, Shift+Enter for newline)
- Resolve / handover actions (resolve auto-triggers CSAT survey on backend)
- SLA badge per conversation (overdue / minutes-left / OK)
- Tenant-aware via JWT claim — backend enforces row-level isolation
- Bangla-first text rendering (`lang="bn"` on message bubbles + composer)

## Develop

```bash
cd agent-inbox
npm install
# Backend must be running on 127.0.0.1:8000 (default)
# To point elsewhere:  echo "VITE_BACKEND_URL=http://staging.example.com" > .env.local
npm run dev
```

Open <http://localhost:5173>. Default admin credentials are bootstrapped by the
backend; first login forces a password change.

## Build for production

```bash
npm run build
# Outputs to ./dist
```

The FastAPI backend (`app/main.py`) auto-mounts this `dist/` directory at
`/admin` if it exists. So in production:

```text
https://your-domain.com/             → tablet/laptop PWA (existing)
https://your-domain.com/admin/       → React agent inbox (this app)
https://your-domain.com/api/...      → FastAPI JSON API (shared)
```

## Folder layout

```
src/
├── api/
│   ├── client.ts        — fetch wrapper + JWT auto-refresh + tenant header
│   └── endpoints.ts     — typed methods for every backend endpoint
├── auth/
│   └── AuthContext.tsx  — login / logout / pwd-change / re-hydrate
├── hooks/
│   └── useEventStream.ts — SSE EventSource with retries
├── pages/
│   ├── LoginPage.tsx
│   ├── PasswordChangePage.tsx
│   └── InboxPage.tsx    — 3-pane shell, SSE wired
├── components/
│   ├── ConversationList.tsx  — left pane
│   ├── ConversationDetail.tsx — center pane (messages + actions)
│   ├── MessageComposer.tsx
│   ├── SlaBadge.tsx
│   └── ReportsPanel.tsx — right pane (live counts + CSAT + SLA)
├── styles/index.css
├── types.ts             — shared response types matching backend
├── App.tsx              — router + protected routes
└── main.tsx             — Vite entry
```

## Security notes
- Tokens live in `localStorage` (industry-standard for SPAs; backend can rotate via Redis blacklist). For high-sensitivity deployments, switch to httpOnly cookies + CSRF token.
- SSE token is passed as query param because EventSource can't set headers; the backend verifies it through the same kid-class-aware decoder + refresh blacklist.
- Refresh-on-401 is single-flight (`refreshing` promise) so a burst of 401s doesn't fan-out into N refresh calls.
- All `/api/*` calls send `X-Tenant-ID` from local storage; backend treats the **JWT claim as authoritative** — header is informational only.
