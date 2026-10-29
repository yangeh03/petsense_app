# GPS in the Location tab

The Location tab shows **室内 UWB 定位** and **GPS 定位** together. They use
independent coordinates and do not switch or fuse automatically. UWB setup,
boundary collection and point-cloud alignment remain available below the maps.

GPS uses the selected collar's authenticated telemetry. The most recent live
frame or REST observation snapshot supplies the GPS object; the existing
`telemetry-service.ts` owns parsing. No phone-location permission is requested.
New no-fix frames clear the map, and server `remainingMs` expires positions even
when no further frames arrive. Replay, unbound collars and other devices cannot
supply a current GPS position. The page shows search/start/error/expired states
without substituting sample coordinates.

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
