# Dashboard Sound Implementation Plan

> Execute inline with superpowers:executing-plans; user has opted out of stepwise confirmations.

**Goal:** Discoverable sound state and meaningful timestamped history, no episode-count KPI.
**Spec:** docs/superpowers/specs/2026-10-26-dashboard-sound-design.md
**Architecture:** Pure sound view selector and grouping over existing normalized frame/events; hero permanent status row; separate history card. Backend contract preserved.

- [x] Add RED/GREEN behavioral tests: fresh/stale/unbound/preparing/quiet/uncertain/vocal states; chronological stable groups merging <=30 seconds, no input mutation or invented duration.
- [x] Replace conditional hero sound chip with permanent diagnosed sound state; remove fragment KPI; independent sound card with timestamped compact grouped events.
- [x] Run full client gates and Web export; computer use mobile UI; review changes and record limits; commit in existing worktree.
