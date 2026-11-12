import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ThemedText } from '@/components/themed-text';
import { useObservations } from '@/hooks/use-observations';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';
import { clockTime, currentPrediction } from '@/services/observation-view';
import { parseTelemetryMessage } from '@/services/telemetry-service';
import { storageSpace } from '@/services/storage-space';

/** Engineering detail is a deliberate second layer, including only fields actually reported. */
export default function MonitorScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { latest, isFresh, now, displayState } = useTelemetry();
  const { dashboard, observations, error, reload } = useObservations('day');
  const [more, setMore] = useState(false);
  const snapshot = observations?.latest
    ? parseTelemetryMessage(
        JSON.stringify({
          topic: 'petsense/sensor/edge_v1',
          received_at: observations.latest.receivedAt,
          payload: observations.latest.payload,
        }),
      )
    : null;
  const device = dashboard?.pet?.device_id;
  const frame =
    latest?.deviceId === device && isFresh
      ? latest
      : snapshot?.deviceId === device
        ? snapshot
        : null;
  const age = frame ? now - Date.parse(frame.receivedAt) : Infinity;
  const gps = frame?.gps;
  const gpsFresh =
    gps?.valid && age >= -5000 && age < gps.remainingMs && displayState !== 'replaying';
  const fresh = !!frame && age >= -5000 && age < 10_000 && displayState !== 'replaying';
  const edge = frame?.edge;
  const prediction = currentPrediction(frame, !!frame);
  const health = frame?.health?.valid ? frame.health : null;
  const unavailable = '未上报';
  return (
    <ScrollView style={{ backgroundColor: theme.background }} contentContainerStyle={styles.scroll}>
      <View style={[styles.container, { paddingTop: insets.top + 20 }]}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="返回"
          onPress={() => router.back()}
          style={styles.tap}
        >
          <ThemedText style={{ color: theme.tint }}>‹ 返回</ThemedText>
        </Pressable>
        <ThemedText style={styles.title}>项圈诊断</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">
          用于排查设备与识别结果；此页保留模型和传感器参数。
        </ThemedText>
        {error ? (
          <Pressable accessibilityRole="button" onPress={() => void reload()}>
            <ThemedText style={{ color: theme.warning }}>{error} 点击重试</ThemedText>
          </Pressable>
        ) : null}
        <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText
            type="smallBold"
            style={{ color: fresh ? theme.online : theme.textSecondary }}
          >
            {fresh ? '设备正在上报' : '当前没有新鲜数据'}
          </ThemedText>
          <Row label="设备" value={device || '未绑定'} />
          <Row
            label="最近接收"
            value={
              frame ? `${clockTime(frame.receivedAt)}${fresh ? '' : ' · 历史记录'}` : unavailable
            }
          />
          <Row
            label="电量"
            value={
              frame?.battery?.found
                ? `${Math.round(frame.battery.percent)}% · ${frame.battery.voltage.toFixed(3)}V`
                : unavailable
            }
          />
        </View>
        <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText style={styles.section}>端侧识别{fresh ? '' : ' · 最近记录'}</ThemedText>
          <Row label="动作" value={frame ? prediction.action : unavailable} />
          <Row
            label="动作置信度"
            value={edge?.action ? `${Math.round(edge.action.confidence * 100)}%` : unavailable}
          />
          <Row
            label="声音"
            value={
              frame
                ? `${prediction.sound}${prediction.experimental ? '（实验性）' : ''}`
                : unavailable
            }
          />
          <Row
            label="声音置信度"
            value={
              edge?.sound?.label === 'quiet'
                ? '安静由能量门限判断'
                : edge?.sound
                  ? `${Math.round(edge.sound.confidence * 100)}%`
                  : unavailable
            }
          />
          <ThemedText type="small" themeColor="textSecondary">
            动作与声音由项圈模型推理，服务端只做存储与片段合并。没有上报时不推断状态。
          </ThemedText>
        </View>
        <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText style={styles.section}>体征传感器{fresh ? '' : ' · 最近记录'}</ThemedText>
          <Row
            label="有效性"
            value={
              frame?.health ? (frame.health.valid ? '硬件标记有效' : '硬件标记无效') : unavailable
            }
          />
          {health && (
            <>
              <Row
                label="心率 / 血氧"
                value={`${health.heartRate || '—'} bpm / ${health.spo2 || '—'}%`}
              />
              <Row
                label="传感器 / 环境温度"
                value={`${health.bodyTemp || '—'}℃ / ${health.envTemp || '—'}℃`}
              />
              <Row
                label="呼吸"
                value={health.respiration > 0 ? `${health.respiration}次/分` : unavailable}
              />
              <Row
                label="HRV（SDNN）"
                value={health.hrvSdnn > 0 ? `${health.hrvSdnn}ms` : unavailable}
              />
            </>
          )}
          <ThemedText type="small" themeColor="textSecondary">
            硬件有效标记不代表已完成临床校准。零值及未支持指标不作为正常测量结果。
          </ThemedText>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ expanded: more }}
          onPress={() => setMore(!more)}
          style={styles.tap}
        >
          <ThemedText type="smallBold" style={{ color: theme.tint }}>
            {more ? '收起技术参数 −' : '展开技术参数 ＋'}
          </ThemedText>
        </Pressable>
        <Row
          label="SD 剩余 / 总容量"
          value={
            fresh
              ? `${storageSpace(edge?.sdFreeBytes)} / ${storageSpace(edge?.sdTotalBytes)}`
              : '等待设备在线'
          }
        />
        {more && (
          <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <Row label="模型" value={edge?.modelVersion || unavailable} />
            <Row
              label="声音 AC RMS"
              value={edge?.acRms !== undefined ? edge.acRms.toFixed(6) : unavailable}
            />
            <ThemedText type="small" themeColor="textSecondary">
              RMS 为相对幅度，不是分贝。当前摘要上报不含可播放的原始录音。
            </ThemedText>
            <Row
              label="推理耗时"
              value={edge?.inferenceUs !== undefined ? `${edge.inferenceUs}µs` : unavailable}
            />
            <Row
              label="SD 卡"
              value={edge?.sdReady === undefined ? unavailable : edge.sdReady ? '就绪' : '未就绪'}
            />
            <Row
              label="实验采集"
              value={
                edge?.captureActive === undefined
                  ? unavailable
                  : edge.captureActive
                    ? '采集中'
                    : '未开启'
              }
            />
            <Row
              label="UWB 测距"
              value={
                frame?.uwb?.valid
                  ? frame.uwb.distanceMm.map((v) => `${v}mm`).join(' / ')
                  : unavailable
              }
            />
            <Row
              label="GPS 状态"
              value={
                gpsFresh
                  ? '已定位'
                  : gps?.valid
                    ? '定位已过期'
                    : {
                        disabled: '已关闭',
                        starting: '启动中',
                        searching: '搜星中',
                        stale: '定位已过期',
                        error: '定位异常',
                      }[gps?.status || ''] || unavailable
              }
            />
            <Row
              label="GPS 经纬度（WGS84）"
              value={
                gpsFresh ? `${gps.latitude?.toFixed(7)}, ${gps.longitude?.toFixed(7)}` : unavailable
              }
            />
            <Row label="GPS UTC" value={gpsFresh ? gps.utc || unavailable : unavailable} />
            <Row
              label="设备运行"
              value={frame ? `${Math.floor(frame.tsMs / 1000)}秒` : unavailable}
            />
          </View>
        )}
      </View>
    </ScrollView>
  );
}
function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
      <ThemedText type="smallBold" style={styles.value}>
        {value}
      </ThemedText>
    </View>
  );
}
const styles = StyleSheet.create({
  scroll: { alignItems: 'center' },
  container: { width: '100%', maxWidth: 640, paddingHorizontal: 24, paddingBottom: 80, gap: 18 },
  title: { fontSize: 28, lineHeight: 36, fontWeight: '700' },
  section: { fontSize: 18, lineHeight: 24, fontWeight: '700' },
  card: { borderRadius: 22, padding: 20, gap: 16 },
  row: { flexDirection: 'row', justifyContent: 'space-between', gap: 20 },
  value: { flex: 1, textAlign: 'right' },
  tap: { paddingVertical: 10, alignSelf: 'flex-start' },
});
