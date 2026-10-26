# Preset PetGIF experience design

## Intent

Bring the teammate's existing PetGIF assets into the PetSense app's pet profile, home screen, and exercise diary. Let users choose a ready-made animation without paying to generate another identity. Keep each user's pet profile, diary inputs, generated result, and community post scoped to that user's PetSense account.

## Goals

- Let a dog profile select or clear one of the approved preset animation templates.
- Show the selected template's animation on the home screen only when a fresh, matching device prediction confidently identifies that action; otherwise show a static portrait and an honest status label.
- Keep custom PetGIF identity (`petgif_id`) separate from the preset animation selection. Diary generation may use only the current user's own `petgif_id`.
- Make diary creation asynchronous from the app's perspective, with visible queued/running/completed/failed states, safe retry after a lost response, and an explicit path to view or share the completed poster.
- Exercise profile, home and diary flows in a simulator against an isolated local database and a mock PetGIF API, so the test does not create paid external media or alter the production account.

## Non-goals

- Triggering any image or video generation for a preset.
- Sending a selected preset's shared PetGIF ID to a diary endpoint.
- Changing indoor/outdoor tracking, route recommendation, or floor-plan rendering.
- Adding custom animation generation or more template assets.

## Constraints

- `Pet.petgif_id` already identifies the individual PetGIF identity used for diary generation. It must retain that meaning.
- A separate nullable `avatar_template_id` identifies the reusable animation choice. Only server-approved template IDs are accepted.
- The external API key stays on the server. The app only calls the authenticated PetSense proxy.
- The external diary endpoint creates a diary under its PetGIF ID and responds asynchronously. The app's long-running job must be tied to an authenticated PetSense user and pet, and duplicate submits must resolve to the existing job.
- Missing, stale, low-confidence, mismatched-device, unsupported-action, failed-media, offline, and unauthenticated states must fall back to the pet's own static portrait/emoji without inventing an observed behavior.
- No default values may imply measured exercise: missing duration or steps are entered by the user; selected photos are optional, size/count limited, and removed from temporary local storage after forwarding.

## Approaches

1. **Separate preset and personal identities (selected).** Store a small preset key on the user's pet; proxy catalog/media from the server; preserve personal `petgif_id` for diary creation. Return a task ID promptly from diary submission and let the app poll authenticated task status. This reuses the existing animation assets, prevents shared diary records, and gives the UI responsive progress and idempotent retries.
2. **Reuse the preset PetGIF ID for both animation and diary.** This needs less profile data but stores one user's photos, notes, and diary in Marni's or winter-gourd's shared history. Rejected because it crosses user ownership.
3. **Generate a fresh PetGIF identity for every app user.** This preserves ownership but charges for the expensive identity generation the presets are meant to avoid. Rejected as the default path.

## Design

### Profile and persistence

Add `avatar_template_id` to the Pet model through an Alembic migration and serialize it as `avatarTemplateId`. Extend the existing profile PATCH allowlist with a validated nullable template ID. The profile view gets a compact template picker with a preview, selected state, clear option, and a note that the preset changes the animation only; it leaves the user's photo and personal PetGIF identity intact. Only dog profiles show the currently available dog presets. Save through the existing profile mutation path, preserve unsaved-edit protection, and notify account-scoped pet listeners after a successful response.

Expose a server-owned allowlist of complete templates and their public media URLs through an authenticated proxy. Cache metadata server-side and fail the catalog gracefully: profile editing remains usable and the home screen falls back to the pet's existing portrait. Never accept arbitrary external PetGIF IDs or media hosts from the app.

### Home animation

The dashboard returns the personal PetGIF and selected preset data as distinct fields. `PetHero` uses the exact action GIF from the personal identity if available, then from the selected preset; it does not substitute a different action. It uses the user's uploaded avatar, personal character card, selected preset character card, or emoji for the still image in that order. A fresh, matching, confident model action controls the GIF. “Fresh”, device matching, action mapping, and confidence threshold reuse the existing `selectPetAvatar` implementation (currently confidence >= 0.6); do not add another inference rule. Offline or uncertain frames keep the still image and corresponding status label. Use Expo Image's animated GIF support and disk/memory cache, a brief cross-fade, stable recycling keys, and existing failed-URL retry behavior to avoid flicker and repeated downloads.

### Exercise diary

Keep the diary entry visible for pets with a personal PetGIF identity. If a user selected only a preset, show a clear locked explanation that their preset is animation-only and diary generation requires a personal PetGIF identity or a future upstream user-scoped template endpoint. Never silently send a preset ID.

Replace the long blocking proxy request with an authenticated job contract scoped to the current PetSense pet: multipart `POST` plus an `Idempotency-Key` returns a stable job ID and queued/running state; authenticated `GET` status returns progress and, when complete, poster URL/stats; the completed history is scoped to that user's pet. The server resolves the user's own `petgif_id` from the database, records task ownership and remote diary ID, and polls the upstream diary endpoint outside the request. Repeating a submit key returns the existing job. Job state is durable so app navigation or process restart does not lose the result; stuck work transitions to an actionable failure/unknown state rather than inviting duplicate paid work.

The modal validates duration (1–600 minutes), steps (0–100,000), notes/place lengths, and at most three supported photos (10 MB each). It prevents duplicate submits, explains the expected wait, shows queued/generating/completed/failed/unknown states, retains job IDs across modal close/reopen, and polls with bounded backoff. An unknown task remains queryable and cannot be blindly resubmitted; only a confirmed pre-upstream failure may be retried with a new idempotency key. Sharing is a separate explicit action; it uses the authenticated user's pet ID and accepts only trusted PetGIF storage URLs. Sharing/API failures keep the generated result available for another attempt. Provide a recent-history list of the user's completed and active jobs in the diary modal so results remain reachable after app restart; retain job rows for 90 days and cap the visible list at 20.

## Implementation calls

- Use the existing authenticated Pet profile and dashboard endpoints rather than adding client-side PetGIF credentials.
- Keep server template IDs in a named constant so adding/removing templates does not change app data semantics.
- Use a durable diary-job table and a bounded worker because one upstream task can take roughly two minutes and must not block an HTTP request or be duplicated after a client timeout.
- Run a bounded `ThreadPoolExecutor` inside the current single-process WSGI service. On startup, resume jobs that have a persisted upstream diary ID; mark jobs with no known upstream ID as unknown rather than automatically resubmitting. Keep unknown jobs available for support reconciliation and block duplicate generation for that request key.
- Local simulator E2E uses a disposable database, test login, and mock upstream that returns ready GIFs and queued/completed diary jobs. It must not invoke the real generation endpoint or write to the installed production account.

## Approval boundary

This design covers the first delivery: pet profile template selection, home animation, and account-scoped asynchronous diary jobs. No product code or schema changes are made until this document is approved.
