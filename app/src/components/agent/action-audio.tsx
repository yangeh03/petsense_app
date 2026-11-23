/** Web 端录音播放（W10 D5）：HTML5 audio + 鉴权查询地址，无新增原生依赖。 */
export function ActionAudio({ url }: { url: string }) {
  return <audio controls preload="none" src={url} style={{ width: '100%', height: 40 }} />;
}
