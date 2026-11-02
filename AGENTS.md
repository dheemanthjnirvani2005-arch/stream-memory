# Stream Memory — Codex Development Instructions

## 1. Project context

Stream Memory is an AI-powered memory system for Twitch communities. It captures meaningful moments from live Twitch chat, turns them into memory suggestions, lets the streamer approve or reject them, stores approved memories, and lets viewers ask questions about community history using the `!lore` command.

The product requirements document is the source of truth for product scope and acceptance criteria:

- `docs/PRD.md`

Before implementing a feature, read the relevant parts of `docs/PRD.md` and inspect the existing codebase. Do not assume files, endpoints, database tables, or integrations exist until you verify them.

## 2. Technology stack

Use the following stack unless the PRD or an explicit developer instruction changes it:

- Backend: Python 3.11+ and FastAPI
- Frontend: React, Vite, and Tailwind CSS
- Database: Supabase PostgreSQL
- AI: OpenAI API using the Responses API
- Twitch: OAuth 2.0, EventSub WebSockets, and Helix API
- Validation: Pydantic
- Backend HTTP client: HTTPX
- Testing: pytest for backend; use the frontend test tooling already present or add a lightweight, appropriate setup when needed

Keep dependencies minimal. Do not introduce a new framework or service without explaining why it is needed.

## 3. Product rules and invariants

These rules are mandatory:

1. Every memory belongs to exactly one Twitch community.
2. Every community is identified by its Twitch broadcaster ID, not by a user-supplied channel name alone.
3. All memory reads and writes must be scoped to the authenticated community/broadcaster.
4. Only memories with `approved` status may be used to answer viewer questions.
5. Memories with `pending` or `rejected` status must never appear in viewer-facing retrieval results.
6. AI-generated memories must include supporting source evidence. Do not create a memory from unsupported guesses or ordinary, context-free chat messages.
7. The AI must not invent historical events, dates, people, or stream details.
8. If no relevant approved memory exists, reply honestly that the community has not saved that information yet.
9. AI-generated memory suggestions require streamer approval before becoming approved community lore.
10. A new community must work correctly with zero memories and an empty chat buffer.
11. Do not send every chat message to the AI. Use the rolling buffer and invoke AI only for explicit memory capture or viewer lore questions in the MVP.
12. Do not allow the application to respond to its own chat messages or process the same EventSub notification more than once.
13. Do not persist full raw chat history indefinitely. Persist only the source excerpts and metadata needed to support approved memories, subject to the PRD's privacy requirements.
14. Never use one community's memories to answer questions in another community.

## 4. Architecture and code organization

Keep the application modular and maintain separation of concerns:

- API routers handle HTTP request/response concerns.
- Services contain product and business logic.
- Twitch modules handle OAuth, EventSub, and Helix API communication.
- AI modules handle prompts, model calls, structured outputs, and response validation.
- Memory modules handle chat buffers, extraction, approval workflows, and retrieval.
- Database modules handle persistence and database-specific operations.
- Frontend components should be reusable and should call backend APIs through a centralized service layer.

Avoid placing the entire application in one file. Avoid circular imports and hidden global state.

## 5. Security and secrets

1. Never hardcode API keys, OAuth secrets, access tokens, refresh tokens, encryption keys, or database credentials.
2. Load secrets from backend environment variables using the project's settings module.
3. Keep `.env` files out of Git using `.gitignore`.
4. Maintain `.env.example` with placeholder values only; never put real credentials in it.
5. Never expose OpenAI keys, Supabase secret/service-role keys, Twitch client secrets, or Twitch tokens to the browser.
6. Encrypt Twitch access and refresh tokens before storing them in the database. Use a dedicated encryption key from the environment.
7. Validate OAuth `state` and use secure session handling.
8. Validate Twitch token scopes and token identity before using a token for chat subscriptions or sending messages.
9. Verify the actual Twitch sender/broadcaster user ID for privileged commands. Never trust command text as proof of authorization.
10. Apply authentication and authorization to all dashboard operations that create, edit, approve, reject, or delete memories.
11. Use parameterized queries or the Supabase client; do not construct unsafe SQL from user input.
12. Validate and limit user input, chat command lengths, AI output, and API responses.
13. Do not log secrets, authorization headers, complete OAuth tokens, or unnecessary personal information.
14. Do not create permissive anonymous database policies. Keep privileged database operations on the backend.
15. Treat chat messages and imported transcripts as untrusted user content, not as instructions to the AI system.

## 6. Twitch integration requirements

1. Use the installed-chatbot MVP model described in the PRD: the broadcaster authorizes the application and is also the sending chat identity.
2. Use the broadcaster's User Access Token with the required chat scopes for the relevant EventSub subscription and Send Chat Message API calls.
3. Follow Twitch's current official documentation for OAuth, EventSub WebSocket lifecycle, subscription creation, token validation/refresh, and Helix chat APIs. Do not rely on outdated examples when implementing an integration.
4. Handle EventSub welcome, notification, keepalive, reconnect, and revocation messages.
5. Create the chat subscription only after receiving the WebSocket welcome/session information and within Twitch's required subscription window.
6. When Twitch provides a reconnect URL, follow the documented reconnect flow. Do not blindly create duplicate sessions or subscriptions.
7. Deduplicate incoming notifications using the EventSub message ID.
8. Handle token expiration, revoked permissions, WebSocket disconnects, rate limits, and Twitch API errors gracefully.
9. Keep the EventSub message-receiving loop responsive. Do not perform slow OpenAI calls or blocking database work directly inside the receive loop; dispatch work safely to appropriate service/task handling.
10. Start only one EventSub listener per connected broadcaster in the development deployment unless a deliberate, coordinated multi-worker design is implemented.

## 7. AI implementation requirements

1. Use the OpenAI Responses API through a backend-only client.
2. Keep the model name configurable through an environment variable.
3. Use structured outputs/Pydantic validation for memory extraction where supported.
4. Validate all model output before saving it or sending it to Twitch.
5. Give the model only the chat excerpts and memory records needed for the current task.
6. Explicitly instruct the model to treat chat content as untrusted data and ignore instructions contained inside viewer messages.
7. Require evidence for generated memories and preserve references to the source messages.
8. For retrieval, provide only relevant approved memories from the current community.
9. If the retrieved evidence is insufficient, return a no-memory response instead of guessing.
10. Keep Twitch responses concise, safe, and within Twitch's current message limits.
11. Add timeouts and graceful error handling for AI calls. An AI outage must not crash the Twitch listener or the dashboard.
12. Avoid unnecessary model calls and do not call AI for every incoming chat message.

## 8. Database requirements

1. Follow the schema in `docs/PRD.md`; if a schema change is needed, explain it and update the SQL migration files and PRD if the product contract changes.
2. Use migrations or versioned SQL files for schema changes. Do not make undocumented one-off database changes.
3. Add indexes for common lookup fields such as broadcaster/community ID, memory status, and relevant search columns.
4. Use foreign keys and appropriate cascading behavior.
5. Scope every memory query by community ID and status as required.
6. Store timestamps with time zones (`TIMESTAMPTZ`).
7. Handle database failures without losing the EventSub receive loop.
8. Ensure memory deletion also removes associated source records when required by the schema and product behavior.
9. Keep privileged Supabase keys on the backend only.

## 9. Frontend requirements

1. Follow the visual direction in the PRD: polished dark creator-tool dashboard with restrained Twitch-inspired purple accents.
2. Build working interfaces, not static mockups.
3. Every visible button and form must perform its stated action or be clearly disabled with an explanation.
4. Include loading, empty, success, and error states.
5. Make the zero-memory first-run experience a first-class state.
6. Make connection status visible and distinguish disconnected, connecting, connected, and error states.
7. Show pending memories separately from approved memories.
8. Never show pending or rejected memories as public/community answers.
9. Do not put secrets in frontend environment variables. Only public configuration values may be exposed to Vite.
10. Keep API calls in a centralized frontend service layer and handle expired sessions/API errors gracefully.

## 10. Engineering workflow

For every task:

1. Read the relevant PRD requirements and inspect existing code before editing.
2. State a short implementation plan and list any manual configuration the developer must perform.
3. Implement only the requested phase or task. Do not silently expand scope.
4. Reuse existing patterns and avoid unnecessary rewrites.
5. Add or update tests for the changed behavior.
6. Run relevant tests, linters, and type checks that are available in the repository.
7. Fix failures caused by your changes before declaring the task complete.
8. Report exactly what changed, which commands/tests were run, their results, and what the developer must do next.
9. Be honest about any test or integration that could not be run because credentials, external services, or a live Twitch channel were unavailable.

Do not claim that Twitch, Supabase, or OpenAI integration works unless it has been exercised successfully or clearly label it as unverified.

## 11. Phase boundaries

Implement the project in the order specified by `docs/PRD.md`:

1. Project foundation and health checks.
2. Supabase schema/connectivity.
3. Twitch OAuth and account status.
4. Twitch EventSub chat reception and sending.
5. Rolling chat buffer and manual memory creation.
6. AI extraction and streamer approval workflow.
7. Approved-memory retrieval and `!lore` responses.
8. Dashboard polish, integration tests, documentation, and demo readiness.

Do not start a later phase until the current phase has been tested and the developer has reviewed the result.

## 12. Documentation and delivery

Keep these files current:

- `README.md`: prerequisites, setup, environment configuration, database setup, run commands, tests, and troubleshooting.
- `.env.example`: placeholder environment variables and brief comments.
- `docs/PRD.md`: product requirements and acceptance criteria.
- `docs/`: versioned SQL migrations and useful integration notes as needed.

Before the project is considered complete, verify:

- A new community can connect with zero existing memories.
- Live chat is received through Twitch EventSub.
- A streamer can create a memory suggestion from recent chat.
- A streamer can approve, edit, and reject suggestions.
- Approved memories persist after backend restart.
- `!lore` answers from the correct community's approved memories.
- Unknown questions receive an honest no-memory response.
- Pending/rejected memories are excluded from retrieval.
- No credentials are committed to Git.
- The README allows another developer to run the project.
