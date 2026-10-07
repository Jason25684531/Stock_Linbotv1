## ADDED Requirements

### Requirement: Authenticated pages shall share a dark quant application shell
The Web application SHALL render Dashboard, Stock Analysis, Strategy Center, Backtest, and System content inside a shared Flask/Jinja shell that preserves the existing dark quant theme, typography, restrained borders, semantic status colors, and current frontend technology stack.

#### Scenario: User opens a primary Web page
- **WHEN** an authenticated user opens any primary Web page
- **THEN** the page renders the shared header, sidebar navigation, and main-content region
- **THEN** the page does not require React, Vue, Next.js, or another frontend framework

### Requirement: Sidebar navigation shall use verified page routes
The shell SHALL provide navigation links for Dashboard, Stock Analysis, Strategy Center, Backtest, and System using implemented Flask routes, and SHALL visually identify the current route.

#### Scenario: Dashboard is active
- **WHEN** the current request route is `/dashboard`
- **THEN** Dashboard has the active blue navigation treatment
- **THEN** all other navigation items use the neutral treatment

#### Scenario: No formal Trades page exists
- **WHEN** the route inventory contains an API Trades endpoint but no formal HTML Trades route
- **THEN** the sidebar does not add a fake Trades destination

### Requirement: Navigation shall adapt without collapsing core content
The shell SHALL provide a fixed sidebar on desktop, a collapsible sidebar on tablet, and a drawer on mobile while preserving horizontally scrollable tables and a usable minimum chart size.

#### Scenario: Mobile user opens navigation
- **WHEN** the viewport is in the mobile breakpoint and the user activates the navigation control
- **THEN** the sidebar opens as an accessible drawer
- **THEN** the main content remains reachable after the drawer closes

#### Scenario: A wide table is shown on a narrow viewport
- **WHEN** a catalog or result table exceeds the mobile content width
- **THEN** its own container scrolls horizontally
- **THEN** the entire page is not forced to an unusable width

### Requirement: Global operational status shall be truthful
The header SHALL display system health from verified backend evidence and SHALL represent unverified health as Unknown or Degraded rather than claiming normal operation.

#### Scenario: Health evidence confirms normal operation
- **WHEN** the configured health contract reports the required system evidence as normal
- **THEN** the header displays `SYSTEM ONLINE` with a green status indicator

#### Scenario: Health evidence is absent or degraded
- **WHEN** required system health evidence is absent, stale, or degraded
- **THEN** the header displays a truthful Unknown or Degraded state
- **THEN** the header does not display a false green online state

### Requirement: Broker-disabled state shall not imply live trading or failure
The UI MUST preserve `BROKER_ORDER_SUBMISSION=DISABLED`, MUST NOT display `LIVE TRADING`, and SHALL present disabled broker submission as expected configuration rather than an application error.

#### Scenario: Broker order submission is disabled
- **WHEN** backend configuration reports broker order submission as disabled
- **THEN** operational UI displays `Broker Order Submission — DISABLED`
- **THEN** the broker state alone does not cause a red system-error presentation
