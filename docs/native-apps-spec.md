# AI Holding — Native Apps Specification

**Version:** 1.0.0  
**Date:** 2026-03-23  
**Status:** Draft

---

## 1. Overview

Native mobile (iOS / Android) and desktop (macOS / Windows / Linux) apps for the AI Holding platform, providing the Owner with full control over the multi-agent system from any device.

### Goals
- Real-time monitoring of agents, companies, and trading from mobile
- Push notifications for approvals, alerts, and agent events
- Chat with CEO and agents natively (not through Telegram)
- Secure authentication with biometrics support
- Offline-capable dashboard with sync

### Non-Goals (v1.0)
- Agent-to-agent chat UI (internal only)
- Plugin/skill development from mobile
- Direct database access

---

## 2. Architecture

### 2.1. Framework
**React Native + Expo** for mobile (iOS + Android from single codebase).  
**Electron + React** for desktop, sharing components with the web dashboard.

### 2.2. Backend Communication
```
┌──────────────┐     HTTPS/WSS      ┌──────────────────┐
│  Native App  │ ◄──────────────────► │  FastAPI Backend  │
│  (RN/Expo)   │     REST + WS       │  (existing)       │
└──────────────┘                     └──────────────────┘
```

- **REST API**: All existing `/api/*` endpoints (agents, companies, dashboard, trading, skills)
- **WebSocket**: Real-time events via existing `/ws` endpoint
- **Authentication**: JWT tokens (see §3)
- **Push Notifications**: Firebase Cloud Messaging (FCM) for Android, Apple Push Notification Service (APNs) for iOS

### 2.3. Project Structure
```
apps/
├── mobile/                    # React Native (Expo)
│   ├── app/                   # Expo Router pages
│   │   ├── (tabs)/
│   │   │   ├── index.tsx      # Overview dashboard
│   │   │   ├── agents.tsx     # Agent management
│   │   │   ├── trading.tsx    # Trading view
│   │   │   ├── chat.tsx       # Chat with agents
│   │   │   └── settings.tsx   # App settings
│   │   ├── agent/[id].tsx     # Agent detail
│   │   ├── company/[id].tsx   # Company detail
│   │   ├── approval/[id].tsx  # Approval decision
│   │   └── login.tsx          # Authentication
│   ├── components/
│   │   ├── AgentCard.tsx
│   │   ├── TradeSignalCard.tsx
│   │   ├── ApprovalCard.tsx
│   │   └── ChatBubble.tsx
│   ├── hooks/
│   │   ├── useAPI.ts          # API client hook
│   │   ├── useWebSocket.ts    # WS connection hook  
│   │   └── useAuth.ts         # Auth state hook
│   ├── lib/
│   │   ├── api.ts             # API client (shared with web)
│   │   ├── auth.ts            # JWT management
│   │   └── storage.ts         # Secure storage wrapper
│   └── app.json               # Expo config
├── desktop/                   # Electron wrapper
│   ├── main.ts                # Electron main process
│   ├── preload.ts             # IPC bridge
│   └── package.json
└── shared/                    # Shared types & utilities
    ├── types.ts               # API response types
    └── constants.ts           # Shared constants
```

---

## 3. Authentication

### 3.1. JWT Authentication Flow

**Prerequisite:** Add JWT auth to the FastAPI backend.

```
Owner opens app → Login screen → Enter password/PIN
→ POST /api/auth/login { password } → { access_token, refresh_token }
→ Store tokens in secure storage (Keychain/Keystore)
→ Include Authorization: Bearer <token> on all requests
→ Auto-refresh before expiration
```

### 3.2. Backend Changes Required

```python
# New: backend/app/api/auth.py

@router.post("/api/auth/login")
async def login(credentials: LoginRequest) -> TokenResponse:
    """Authenticate owner and issue JWT tokens."""
    # Verify against OWNER_PASSWORD_HASH in env
    # Return access_token (15min) + refresh_token (7d)

@router.post("/api/auth/refresh")
async def refresh_token(token: RefreshRequest) -> TokenResponse:
    """Refresh an expired access token."""

@router.get("/api/auth/me")
async def get_current_user(user = Depends(get_current_user)):
    """Get current authenticated user info."""
```

### 3.3. Security Requirements
- Passwords hashed with bcrypt (cost factor 12+)
- JWT signed with RS256 (asymmetric) or HS256 with 256-bit secret
- Access tokens: 15-minute expiry
- Refresh tokens: 7-day expiry, single-use, rotate on refresh
- Biometric unlock (Face ID / fingerprint) to decrypt stored tokens
- All API requests over HTTPS only
- Rate limiting on login endpoint (5 attempts / 5 minutes)

---

## 4. Screens & Features

### 4.1. Overview Dashboard (Tab 1)
- Agent status cards (thinking/idle/acting/error)
- Company count + active agents count
- Today's spend vs budget (progress bar)
- Pending approvals badge
- Recent activity feed (last 10 events via WebSocket)

### 4.2. Agents (Tab 2)
- List of all agents with status indicators
- Tap → Agent detail (role, model, company, tools)
- Start / Stop / Pause controls
- Chat with agent inline

### 4.3. Trading (Tab 3)
- Portfolio summary (balance, P&L, positions)
- Trade signals with approve/reject swipe actions
- Trade history list
- Mini candlestick charts (TradingView lightweight)

### 4.4. Chat (Tab 4)
- Chat interface with CEO and any agent
- Message bubbles with timestamps
- Tool call indicators (browsing, executing code, searching)
- Voice-to-text input (platform native)

### 4.5. Settings (Tab 5)
- Model tier configuration
- Budget controls
- Notification preferences
- Server connection URL
- Logout / switch accounts

### 4.6. Push Notifications
| Event | Priority | Sound |
|-------|----------|-------|
| Approval requested | High | Alert |
| Trade signal pending | High | Alert |
| Agent error | Medium | Default |
| Daily report ready | Low | None |
| Budget threshold (80%, 100%) | High | Alert |

---

## 5. Offline Support

### 5.1. Cached Data
- Agent list + statuses (refreshed on app open)
- Recent chat messages (last 50 per agent)
- Dashboard overview snapshot
- Pending approval queue

### 5.2. Sync Strategy
- On app resume: diff-sync with backend
- Offline approval decisions queued, synced when connected
- WebSocket auto-reconnect with exponential backoff

---

## 6. Key Dependencies

| Package | Purpose | Version |
|---------|---------|---------|
| expo | Development platform | ~52.x |
| expo-router | File-based routing | ~4.x |
| expo-secure-store | Token storage | ~14.x |
| expo-notifications | Push notifications | ~0.30.x |
| react-native-reanimated | Animations | ~3.x |
| @tanstack/react-query | Data fetching + cache | ~5.x |
| zustand | State management | ~5.x |
| nativewind | Tailwind CSS for RN | ~4.x |

---

## 7. API Changes Summary

### 7.1. New Endpoints Required
```
POST /api/auth/login              # JWT authentication
POST /api/auth/refresh            # Token refresh
GET  /api/auth/me                 # Current user info
POST /api/notifications/register  # Register device for push
DELETE /api/notifications/register # Unregister device
GET  /api/dashboard/snapshot      # Lightweight dashboard snapshot for offline
```

### 7.2. Existing Endpoint Modifications
- All endpoints: Add `Authorization: Bearer` header validation
- WebSocket: Support token-based auth in query params (`/ws?token=...`)
- Add `If-Modified-Since` / `ETag` headers for efficient polling

---

## 8. Implementation Phases

### Phase A: Auth + Core (2 weeks)
1. Add JWT authentication to backend
2. Scaffold Expo project with Expo Router
3. Login screen + secure token storage
4. Overview dashboard screen
5. Agent list + detail screens

### Phase B: Interactive Features (2 weeks)
1. Chat with agents screen
2. Trading dashboard screen  
3. Approval management with swipe actions
4. WebSocket integration for real-time updates
5. Pull-to-refresh on all data screens

### Phase C: Native Features (1 week)
1. Push notifications (FCM + APNs)
2. Biometric authentication
3. Offline data caching
4. Background sync
5. App icon + splash screen

### Phase D: Desktop + Polish (1 week)
1. Electron wrapper sharing React components
2. System tray integration
3. Native notifications
4. Auto-update mechanism
5. Platform-specific testing + store submission prep

---

## 9. Testing Strategy

- **Unit Tests**: Jest + React Native Testing Library for components
- **Integration Tests**: Detox (E2E) for critical flows (login → overview → approve)
- **API Tests**: Existing pytest suite covers backend
- **Manual Testing**: TestFlight (iOS) + Internal Testing Track (Android)

---

## 10. Release Targets

| Platform | Distribution |
|----------|-------------|
| iOS | TestFlight → App Store |
| Android | Internal Track → Play Store |
| macOS | DMG / Homebrew |
| Windows | NSIS installer / Microsoft Store |
| Linux | AppImage / Snap |
