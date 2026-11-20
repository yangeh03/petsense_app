import { leafletCss, leafletJs } from '@/generated/leaflet-assets';

import type { DogMapCoverage } from '@/types/dog-map';

export type DogMapDocumentPoint = {
  /** WGS-84 经纬度；项圈坐标不参与本地图。 */
  longitude: number;
  latitude: number;
  /** 源评分，仅用于标注与聚合代表点选择。 */
  score: number;
};

export type DogMapConfig = {
  tint: string;
  background: string;
  textSecondary: string;
  tileUrl: string;
  coverage: DogMapCoverage;
  points: DogMapDocumentPoint[];
};

/**
 * 遛狗地图文档：本地 Leaflet + 远程 WGS84 瓦片。入口自适应覆盖范围缩放；
 * 低层级按网格聚合密集点，点击单点回传选中；离开覆盖范围提示可一键复位。
 * 不加载外部脚本，不访问上游评分页面。
 */
export function dogMapDocument(config: DogMapConfig) {
  const payload = JSON.stringify({
    tint: config.tint,
    background: config.background,
    textSecondary: config.textSecondary,
    tileUrl: config.tileUrl,
    coverage: config.coverage,
    points: config.points.map((point) => [point.longitude, point.latitude, point.score]),
  }).replace(/</g, '\\u003c');
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'">
<style>${leafletCss}</style><style>html,body,#map{height:100%;width:100%;margin:0}#map{background:${config.background}}.leaflet-control-attribution{font-size:10px}
.pin,.cluster{background:transparent;border:0}.pin span{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:${config.tint};color:#fff;font:600 12px/1 -apple-system,PingFang SC,sans-serif;font-variant-numeric:tabular-nums;box-shadow:0 1px 4px rgba(0,0,0,.25)}
.pin.selected span{outline:3px solid ${config.background};outline-offset:1px}
.cluster span{display:grid;place-items:center;min-width:26px;height:26px;padding:0 5px;border-radius:13px;background:${config.textSecondary};color:#fff;font:600 11px/1 -apple-system,PingFang SC,sans-serif;font-variant-numeric:tabular-nums}</style></head>
<body><div id="map" aria-label="遛狗评分地图"></div><script>${leafletJs}</script><script>
(function(){
  const config=${payload};
  function notify(type,value){
    const data=JSON.stringify(value===undefined?{type}:{type,value});
    if(window.ReactNativeWebView)window.ReactNativeWebView.postMessage(data);
    else window.parent.postMessage(data,'*');
  }
  const map=L.map('map',{zoomControl:true,attributionControl:true,minZoom:10,maxZoom:19});
  let tileLoaded=false,errorTimer=null;
  const tiles=L.tileLayer(config.tileUrl,{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'}).addTo(map);
  function fail(){if(!tileLoaded)notify('tile-error');}
  tiles.on('tileload',function(){tileLoaded=true;clearTimeout(errorTimer);notify('tiles-ready');});
  tiles.on('tileerror',fail);
  tiles.on('loading',function(){tileLoaded=false;clearTimeout(errorTimer);errorTimer=setTimeout(fail,12000);});
  const layer=L.layerGroup().addTo(map);
  const bounds=L.latLngBounds([[config.coverage.south,config.coverage.west],[config.coverage.north,config.coverage.east]]);
  // 覆盖范围外扩 25%，用于判断用户是否已浏览到无评分区域。
  const padded=L.latLngBounds(
    [[config.coverage.south-(config.coverage.north-config.coverage.south)*.25,config.coverage.west-(config.coverage.east-config.coverage.west)*.25],
     [config.coverage.north+(config.coverage.north-config.coverage.south)*.25,config.coverage.east+(config.coverage.east-config.coverage.west)*.25]]);
  let selected=-1;
  const DETAIL_ZOOM=16,CELL=34;
  function pin(index){
    const p=config.points[index];
    return L.marker([p[1],p[0]],{icon:L.divIcon({className:'pin',html:'<span>'+p[2]+'</span>',iconSize:[30,30],iconAnchor:[15,15]}),keyboard:false})
      .on('click',function(){select(index);})
      .addTo(layer);
  }
  function select(index){
    selected=index;notify('select',index);
    layer.eachLayer(function(m){
      if(m.getElement){const el=m.getElement();if(el)el.classList.toggle('selected',m.options.idx===index);}
    });
  }
  function render(){
    layer.clearLayers();
    if(map.getZoom()>=DETAIL_ZOOM){
      config.points.forEach(function(_,index){
        const m=pin(index);m.options.idx=index;
        if(index===selected){const el=m.getElement();if(el)el.classList.add('selected');}
      });
      return;
    }
    const zoom=map.getZoom(),cells={};
    config.points.forEach(function(p,index){
      const pt=map.project([p[1],p[0]],zoom);
      const key=Math.floor(pt.x/CELL)+':'+Math.floor(pt.y/CELL);
      const cur=cells[key];
      if(!cur)cells[key]={index:index,count:1,score:p[2]};
      else{cur.count++;if(p[2]>cur.score){cur.index=index;cur.score=p[2];}}
    });
    Object.values(cells).forEach(function(cell){
      const count=cell.count>99?'99+':cell.count;
      const p=config.points[cell.index];
      L.marker([p[1],p[0]],{icon:L.divIcon({className:'cluster',html:'<span>'+(cell.count>1?count:p[2])+'</span>',iconSize:[30,26],iconAnchor:[15,13]}),keyboard:false})
        .on('click',function(){map.setView([p[1],p[0]],Math.min(19,map.getZoom()+2));})
        .addTo(layer);
    });
  }
  map.on('zoomend movestart',function(){notify('drag-start');});
  map.on('moveend',function(){
    notify('drag-end');
    notify(padded.contains(map.getCenter())?'in-coverage':'out-of-coverage');
    render();
  });
  map.fitBounds(bounds,{padding:[24,24]});
  window.petSenseDogReset=function(){map.fitBounds(bounds,{padding:[24,24]});};
  window.petSenseDogFocus=function(index){
    const p=config.points[index];
    if(!p)return;
    map.setView([p[1],p[0]],Math.max(DETAIL_ZOOM,map.getZoom()));
    render();select(index);
  };
  window.addEventListener('message',function(event){
    if(event.source!==window.parent)return;
    let data;try{data=typeof event.data==='string'?JSON.parse(event.data):event.data;}catch(e){return;}
    if(data&&data.type==='dog-reset')window.petSenseDogReset();
    if(data&&data.type==='dog-focus')window.petSenseDogFocus(data.index);
  });
  document.addEventListener('visibilitychange',function(){if(!document.hidden)map.invalidateSize();});
  render();
  notify('ready');
})();</script></body></html>`;
}
