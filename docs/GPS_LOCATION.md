# GPS in the Location tab

The Location tab has an explicit **室内 / 室外** segmented control. The choice is
the user's and persists locally (default indoor on first install); the page
never switches modes automatically and the two coordinate sources are never
mixed. Only the active mode renders a map.

**室外（outdoor）** shows the selected collar's GPS. The most recent live frame
or REST observation snapshot supplies the GPS object; the existing
`telemetry-service.ts` owns parsing. No phone-location permission is requested.
A fix is shown only while the server `remainingMs` is unexpired — stale fixes
hide the marker instead of pretending a position. Raw latitude/longitude and
GNSS UTC are collapsed behind a 坐标详情 toggle. 回到项圈位置 (recenter) and
tile retry are preserved. Replay, unbound collars and other devices cannot
supply a current GPS position. The 室外 view also hosts the 遛狗地图 row
(see `docs/DOG_MAP.md`); it is usable without a bound collar or fix.

**室内（indoor）** shows the calibrated UWB map via the authenticated snapshot
path (`usePositionMap`). There are no live trails — the map draws only the
current (or last known) position; when freshness is lost the marker turns
muted and the status chip labels it 最后位置 with the server-provided expiry
time. Missing states are explicit: 未绑定 (bind entry), 未标定 / waiting with
an actionable setup entry, and a retry row when snapshots fail to update.

UWB setup (touch anchors → boundary walk → align/preview/confirm publish) has
moved out of the tab into the dedicated stack route `/uwb-setup` (室内地图
设置). The workflow, device ownership and server-backed session recovery are
unchanged — capture state survives leaving and re-entering. After a publish,
the location tab refreshes its map on focus. Details:
`docs/uwb-guided-setup.md`, `docs/uwb-walk-alignment.md`.

The GPS map uses **Leaflet 1.9.4** (BSD-2-Clause) and OpenStreetMap WGS84 tiles.
It follows updates until the user pans; **回到项圈位置** restores following.
Latitude, longitude and GNSS UTC remain visible when tile loading fails, with
a retry button. The OpenStreetMap attribution stays visible on the map.

Leaflet JS/CSS are bundled locally in `app/src/generated/leaflet-assets.ts`,
with their license beside it. There is no runtime CDN script dependency. To
regenerate after deliberately updating Leaflet:

```sh
cd app
node tools/generate-leaflet-assets.cjs
```

Native uses `react-native-webview` (Expo SDK 58 version 14.0.1); web uses an
iframe. **Existing standalone native installations need a new native build**
to include WebView; the usual EAS fingerprint runtime policy then applies.
JavaScript exports do not replace iOS/Android device validation.

The default tile URL is `https://tile.openstreetmap.org/{z}/{x}/{y}.png`.
Override `EXPO_PUBLIC_GPS_TILE_URL` at build time with a compatible **HTTPS,
WGS84/Web Mercator** tile provider when needed. Changing to GCJ-02/BD-09 tiles
also requires coordinate conversion; do not overlay raw GNSS onto those tiles.
OpenStreetMap's public tiles are best effort, require attribution, identifiable
native User-Agent / browser Referer and normal caching, and prohibit bulk
prefetch. This implementation uses only viewport requests and normal caching.

References: [Leaflet](https://leafletjs.com/),
[Expo WebView](https://docs.expo.dev/versions/v58.0.0/sdk/webview/),
[OSM tile policy](https://operations.osmfoundation.org/policies/tiles/).

Validation: app lint, strict TypeScript, Prettier, unit regressions and Expo web /
native JavaScript exports. Isolated browser verification uses explicitly labeled
test coordinates; real collar GNSS reception still needs the board connected.
