# Confirmed display plan before positioning calibration

`GET /api/app/uwb/plan?deviceId=...` returns an authenticated, owner-bound display
plan independently of the metre-scaled `/map` endpoint. When no calibrated metric map is bound, the Location screen
shows this approved image with “底图已确认 · 待定位标定”, and keeps GPS available.
It does not put an old map's position, trail or anchors on the new image.

Runtime files live at `<PETSENSE_ROOT>/uwb_plans/` (override with
`PETSENSE_UWB_PLAN_ROOT`). They are user data and are not bundled into Git:

```json
{"devices":{"DEVICE_ID":{"directory":"PLAN_DIRECTORY","ownerId":1,"claimedAt":"EXACT_CLAIM_TIMESTAMP"}}}
```

Each directory contains `plan.json` and `floorplan.png`. A manifest has
`schemaVersion: 1`, `planId`, `name`, `units: "relative"`,
`calibrationStatus: "required"`, and `image: {file, width, height}`.
Bindings must match the current owner and exact `claimedAt`; transfer or
reclaim invalidates the old plan. PNG dimensions, decoding, local paths and
size limits are checked before returning an inline authenticated image.

The user-confirmed 2026-10-29 plan is based on the P1 ground and the bottom
40% height band. The repaired color texture is registered to the original
1009×558 raster using measured image features, then clipped to the extracted
envelope. Registration and original boundaries are stored alongside the image.
Texture repair is for display and is not new geometric evidence.

The server asset and device binding can be prepared before deployment, but
the new endpoint and App display require the display-plan release. Old servers
return 404; the App then retains the existing calibrated-map flow.

After UWB calibration/alignment, publish a genuine metric map bundle. The calibrated
map takes precedence automatically on returning to the Location screen; the display
plan remains a fallback. When switching scenes, archive and remove only this device's
old metric binding before enabling its new owner-bound display plan.
Do not relabel reconstruction units as metres or reuse the old room's transform.

2026-11-26: blue studio is the second relative plan (P1 ground, bottom 40%).
It has no previously confirmed UWB transform. Keep first-room maps and seven
regions, clear only the active metric binding when switching, and calibrate the
studio using its own point cloud and fresh anchor/walk measurements.
