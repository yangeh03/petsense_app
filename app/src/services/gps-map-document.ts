import { leafletCss, leafletJs } from '@/generated/leaflet-assets';

/** Local Leaflet + remote WGS84 OSM tiles. No location permission or phone GPS. */
export function gpsMapDocument(tint: string, background: string, tileUrl: string) {
  const config = JSON.stringify({ tint, background, tileUrl }).replace(/</g, '\\u003c');
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'">
<style>${leafletCss}</style><style>html,body,#map{height:100%;width:100%;margin:0}#map{background:${background}}.leaflet-control-attribution{font-size:10px}</style></head>
<body><div id="map" aria-label="项圈 GPS 地图"></div><script>${leafletJs}</script><script>
(function(){
  const config=${config};
  function notify(type){
    const data=JSON.stringify({type});
    if(window.ReactNativeWebView)window.ReactNativeWebView.postMessage(data);
    else window.parent.postMessage(data,'*');
  }
  const map=L.map('map',{zoomControl:true,attributionControl:true});
  let marker=null,follow=true,tileLoaded=false,errorTimer=null;
  const tiles=L.tileLayer(config.tileUrl,{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'}).addTo(map);
  function fail(){if(!tileLoaded)notify('tile-error');}
  tiles.on('tileload',function(){tileLoaded=true;clearTimeout(errorTimer);notify('tiles-ready');});
  tiles.on('tileerror',fail);
  tiles.on('loading',function(){tileLoaded=false;clearTimeout(errorTimer);errorTimer=setTimeout(fail,12000);});
  map.on('dragstart',function(){follow=false;});
  map.on('movestart',function(){notify('drag-start');});
  map.on('moveend',function(){notify('drag-end');});
  window.petSenseGps=function(point){
    if(!point||!Number.isFinite(point.latitude)||!Number.isFinite(point.longitude)||Math.abs(point.latitude)>90||Math.abs(point.longitude)>180)return;
    const pos=[point.latitude,point.longitude];
    if(!marker){
      marker=L.circleMarker(pos,{radius:9,color:config.tint,fillColor:config.tint,fillOpacity:0.9,weight:3}).addTo(map);
      map.setView(pos,17);
      errorTimer=setTimeout(fail,12000);
    } else {marker.setLatLng(pos);if(follow)map.panTo(pos,{animate:false});}
    map.invalidateSize();
  };
  window.petSenseGpsCenter=function(){if(marker){follow=true;map.setView(marker.getLatLng(),17);}};
  window.addEventListener('message',function(event){
    if(event.source!==window.parent)return;
    let data;try{data=typeof event.data==='string'?JSON.parse(event.data):event.data;}catch(e){return;}
    if(data&&data.type==='gps-position')window.petSenseGps(data.point);
    if(data&&data.type==='gps-center')window.petSenseGpsCenter();
  });
  document.addEventListener('visibilitychange',function(){if(!document.hidden)map.invalidateSize();});
  notify('ready');
})();</script></body></html>`;
}
