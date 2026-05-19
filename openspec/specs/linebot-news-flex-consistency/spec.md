## Purpose
Define a shared Flex rendering contract for news and macro summaries across interactive and scheduled LINE surfaces.

## Requirements

### Requirement: News and macro summaries shall use one shared Flex format
The system SHALL render scheduled and interactive news or macro-summary content through one shared Flex summary builder so the visual presentation stays consistent across channels.

#### Scenario: Interactive news summary is requested
- **WHEN** a user requests on-demand news or macro summary content from the LINE Bot
- **THEN** the response is rendered by the shared news-summary Flex builder
- **THEN** the response does not fall back to a custom one-off layout when the summary content is available

#### Scenario: Morning and evening push jobs send summary content
- **WHEN** the morning or evening LINE push flow includes news or macro-summary content
- **THEN** the job composes that summary section from the same shared news-summary Flex builder used by interactive replies
- **THEN** the news section remains visually consistent between morning and evening pushes

### Requirement: Shared summary Flex content shall preserve readable structure
The shared news-summary Flex builder MUST preserve a consistent information hierarchy for summary title, key outline items, and AI commentary.

#### Scenario: Summary contains multiple headline blocks and AI commentary
- **WHEN** the news summary text includes multiple highlighted points and a final AI commentary block
- **THEN** the Flex layout surfaces the title/header, headline outline items, and AI commentary with consistent visual grouping
- **THEN** the same hierarchy is reused regardless of whether the summary is shown in a standalone reply or inside a scheduled push payload
