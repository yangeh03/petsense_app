import Svg, {
  Circle,
  Defs,
  Ellipse,
  G,
  Path,
  Pattern,
  Polyline,
  RadialGradient,
  Rect,
  Stop,
  Text,
} from 'react-native-svg';
import { useEffect, useRef, useState } from 'react';

/**
 * 家庭俯视户型图 + 宠物实时移动引擎（自 Petsense-demo 完整移植）。
 * 位置数据为演示路线（写死的巡游路径 + 行为状态机）；后续接入真实 UWB 时
 * 由 positionFromUwb 解算坐标替换 route 驱动即可，渲染层不变。
 */

export type LiveBehavior = '站立' | '奔跑' | '坐着' | '趴着';

type Point = { x: number; y: number; room: string; behavior: LiveBehavior };

export type PlanTelemetry = {
  room: string;
  behavior: LiveBehavior;
};

const route: Point[] = [
  { x: 470, y: 520, room: '客厅', behavior: '站立' },
  { x: 430, y: 585, room: '客厅', behavior: '奔跑' },
  { x: 500, y: 645, room: '客厅', behavior: '奔跑' },
  { x: 690, y: 645, room: '客厅', behavior: '站立' },
  { x: 790, y: 610, room: '客厅', behavior: '奔跑' },
  { x: 805, y: 500, room: '客厅', behavior: '奔跑' },
  { x: 805, y: 385, room: '客厅', behavior: '站立' },
  { x: 740, y: 345, room: '客厅', behavior: '奔跑' },
  { x: 650, y: 365, room: '客厅', behavior: '奔跑' },
  { x: 545, y: 385, room: '客厅', behavior: '坐着' },
  { x: 475, y: 455, room: '客厅', behavior: '奔跑' },
  { x: 445, y: 610, room: '客厅', behavior: '奔跑' },
  { x: 520, y: 710, room: '阳台', behavior: '奔跑' },
  { x: 650, y: 725, room: '阳台', behavior: '奔跑' },
  { x: 780, y: 710, room: '阳台', behavior: '站立' },
  { x: 760, y: 650, room: '客厅', behavior: '奔跑' },
  { x: 600, y: 650, room: '客厅', behavior: '奔跑' },
  { x: 470, y: 625, room: '客厅', behavior: '奔跑' },
  { x: 380, y: 590, room: '客厅', behavior: '奔跑' },
  { x: 330, y: 560, room: '主卧', behavior: '奔跑' },
  { x: 315, y: 620, room: '主卧', behavior: '趴着' },
  { x: 325, y: 455, room: '主卧', behavior: '奔跑' },
  { x: 385, y: 390, room: '客厅', behavior: '奔跑' },
  { x: 500, y: 355, room: '客厅', behavior: '奔跑' },
  { x: 580, y: 300, room: '厨房', behavior: '奔跑' },
  { x: 680, y: 260, room: '厨房', behavior: '站立' },
  { x: 770, y: 250, room: '厨房', behavior: '坐着' },
  { x: 770, y: 160, room: '厨房', behavior: '奔跑' },
  { x: 650, y: 220, room: '厨房', behavior: '奔跑' },
  { x: 570, y: 300, room: '厨房', behavior: '奔跑' },
  { x: 500, y: 400, room: '客厅', behavior: '奔跑' },
  { x: 470, y: 520, room: '客厅', behavior: '站立' },
];

function roomAt(x: number, y: number) {
  if (y >= 690 && x >= 350) return '阳台';
  if (x <= 345 && y <= 315) return '次卧';
  if (x > 345 && x < 520 && y <= 300) return '卫生间';
  if (x >= 520 && y <= 305) return '厨房';
  if (x <= 350 && y > 315) return '主卧';
  return '客厅';
}

export function FloorPlan({
  isLive,
  floor,
  targetPoint,
  targetTick,
  onTelemetry,
  petEmoji = '🐕',
  mapStyle,
}: {
  isLive: boolean;
  floor: 1 | 2;
  targetPoint: { x: number; y: number } | null;
  targetTick: number;
  onTelemetry: (value: PlanTelemetry) => void;
  petEmoji?: string;
  /** real = 实景照片底图（隐藏矢量房间层），cartoon = 卡通矢量层 */
  mapStyle: 'real' | 'cartoon';
}) {
  const [position, setPosition] = useState<Point>(route[0]);
  const [angle, setAngle] = useState(0);
  const [trail, setTrail] = useState<Point[]>(route.slice(0, 1));
  const [pulse, setPulse] = useState(false);
  const positionRef = useRef<Point>(route[0]);
  const targetRef = useRef<Point>(route[1]);
  const routeIndexRef = useRef(1);
  const manualTargetRef = useRef(false);
  const dwellUntilRef = useRef(0);
  const callbackRef = useRef(onTelemetry);
  const telemetryKeyRef = useRef('');
  const lastTrailRef = useRef(0);

  useEffect(() => {
    callbackRef.current = onTelemetry;
  }, [onTelemetry]);

  // 点击指挥：设置手动目标点
  useEffect(() => {
    if (!targetPoint) return;
    targetRef.current = {
      x: targetPoint.x,
      y: targetPoint.y,
      room: roomAt(targetPoint.x, targetPoint.y),
      behavior: '站立',
    };
    manualTargetRef.current = true;
    dwellUntilRef.current = 0;
  }, [targetPoint, targetTick]);

  // 光晕呼吸（对应 demo 的 svg-pulse 动画）
  useEffect(() => {
    const timer = setInterval(() => setPulse((value) => !value), 900);
    return () => clearInterval(timer);
  }, []);

  // 移动引擎：rAF 逐帧走向目标，抵达后按行为停留再取下一路线点
  useEffect(() => {
    if (!isLive || floor !== 1) {
      const current = positionRef.current;
      callbackRef.current({ room: roomAt(current.x, current.y), behavior: '站立' });
      return;
    }

    let frame = 0;
    let previous = performance.now();

    const publishTelemetry = (point: Point, behavior: LiveBehavior) => {
      const next = { room: roomAt(point.x, point.y), behavior };
      const key = `${next.room}-${next.behavior}`;
      if (key !== telemetryKeyRef.current) {
        telemetryKeyRef.current = key;
        callbackRef.current(next);
      }
    };

    const tick = (now: number) => {
      const dt = Math.min((now - previous) / 1000, 0.05);
      previous = now;
      const current = positionRef.current;
      const target = targetRef.current;
      const dx = target.x - current.x;
      const dy = target.y - current.y;
      const distance = Math.hypot(dx, dy);

      if (now < dwellUntilRef.current) {
        frame = requestAnimationFrame(tick);
        return;
      }

      if (distance < 5) {
        const isManualArrival = manualTargetRef.current;
        const arrivalBehavior: LiveBehavior = isManualArrival ? '站立' : target.behavior;
        const arrived: Point = {
          ...target,
          room: roomAt(target.x, target.y),
          behavior: arrivalBehavior,
        };
        positionRef.current = arrived;
        setPosition(arrived);

        if (arrivalBehavior !== '奔跑') {
          const dwellTime =
            arrivalBehavior === '趴着' ? 4200 : arrivalBehavior === '坐着' ? 3200 : 2200;
          dwellUntilRef.current = now + dwellTime;
          publishTelemetry(arrived, arrivalBehavior);
        }

        if (manualTargetRef.current) {
          manualTargetRef.current = false;
          const nearest = route.reduce(
            (best, point, index) => {
              const d = Math.hypot(point.x - arrived.x, point.y - arrived.y);
              return d < best.distance ? { index, distance: d } : best;
            },
            { index: 0, distance: Number.POSITIVE_INFINITY },
          );
          routeIndexRef.current = (nearest.index + 1) % route.length;
        } else {
          routeIndexRef.current = (routeIndexRef.current + 1) % route.length;
        }
        targetRef.current = route[routeIndexRef.current];
      } else {
        const speed = manualTargetRef.current ? 135 : 92;
        const step = Math.min(speed * dt, distance);
        const next: Point = {
          x: current.x + (dx / distance) * step,
          y: current.y + (dy / distance) * step,
          room: roomAt(current.x + (dx / distance) * step, current.y + (dy / distance) * step),
          behavior: '奔跑',
        };
        positionRef.current = next;
        setPosition(next);
        setAngle((Math.atan2(dy, dx) * 180) / Math.PI);

        if (now - lastTrailRef.current > 360) {
          lastTrailRef.current = now;
          setTrail((items) => [...items.slice(-16), next]);
        }

        publishTelemetry(next, '奔跑');
      }

      frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [isLive, floor]);

  const wall = '#697576';
  const vectorVisible = mapStyle === 'cartoon';

  return (
    <Svg
      viewBox="0 0 900 800"
      preserveAspectRatio="none"
      style={{ width: '100%', height: '100%' }}
      pointerEvents="none"
    >
      <Defs>
        <Pattern id="tile" width="28" height="28" patternUnits="userSpaceOnUse">
          <Rect width="28" height="28" fill="#f5f5f2" />
          <Path d="M28 0H0V28" fill="none" stroke="#e5e8e6" strokeWidth="1" />
        </Pattern>
        <Pattern id="wood" width="56" height="18" patternUnits="userSpaceOnUse">
          <Rect width="56" height="18" fill="#eee3cf" />
          <Path d="M0 17.5H56 M27 0V18" stroke="#dfceb1" strokeWidth="1" opacity="0.72" />
        </Pattern>
        <RadialGradient id="markerGlow" cx="0.5" cy="0.5" r="0.5">
          <Stop offset="0" stopColor="#ff7b70" stopOpacity="0.45" />
          <Stop offset="1" stopColor="#ff7b70" stopOpacity="0" />
        </RadialGradient>
      </Defs>

      {/* 矢量房间层：仅卡通模板显示（真实模板用照片底图） */}
      {vectorVisible && (
        <G>
          <Rect
            x="40"
            y="48"
            width="302"
            height="265"
            rx="8"
            fill="url(#wood)"
            stroke={wall}
            strokeWidth="16"
          />
          <Rect
            x="350"
            y="48"
            width="162"
            height="244"
            rx="8"
            fill="#e9f0f2"
            stroke={wall}
            strokeWidth="16"
          />
          <Rect
            x="520"
            y="48"
            width="330"
            height="255"
            rx="8"
            fill="url(#tile)"
            stroke={wall}
            strokeWidth="16"
          />
          <Rect
            x="40"
            y="321"
            width="312"
            height="363"
            rx="8"
            fill="url(#wood)"
            stroke={wall}
            strokeWidth="16"
          />
          <Rect
            x="360"
            y="310"
            width="490"
            height="374"
            rx="8"
            fill="#f3ede3"
            stroke={wall}
            strokeWidth="16"
          />
          <Rect
            x="360"
            y="692"
            width="490"
            height="75"
            rx="8"
            fill="url(#tile)"
            stroke={wall}
            strokeWidth="16"
          />
        </G>
      )}

      {/* 家具 + 活动区：仅卡通模板显示 */}
      {vectorVisible && (
        <G opacity="0.9">
          <G transform="translate(75 78)">
            <Rect width="176" height="102" rx="14" fill="#d9d9d6" />
            <Rect x="10" y="9" width="156" height="31" rx="9" fill="#f7f5ef" />
            <Rect x="0" y="44" width="176" height="58" rx="10" fill="#cbd2d4" />
          </G>
          <G transform="translate(72 390)">
            <Rect width="188" height="185" rx="15" fill="#d6d6d2" />
            <Rect x="12" y="12" width="164" height="40" rx="11" fill="#faf7ef" />
            <Rect x="0" y="58" width="188" height="127" rx="11" fill="#cfd4d4" />
          </G>
          <G transform="translate(575 78)">
            <Rect width="60" height="62" rx="12" fill="#c7d4d8" />
            <Rect
              x="10"
              y="10"
              width="40"
              height="34"
              rx="10"
              fill="#eef3f4"
              stroke="#9aaeb5"
              strokeWidth="3"
            />
            <Rect x="210" y="16" width="34" height="128" rx="7" fill="#d3d8d7" />
            <Circle cx="227" cy="57" r="11" fill="#303b3b" />
            <Circle cx="227" cy="103" r="11" fill="#303b3b" />
          </G>
          <G transform="translate(395 420)">
            <Rect width="118" height="180" rx="27" fill="#dedad2" />
            <Rect x="13" y="20" width="38" height="140" rx="16" fill="#f7f5ef" />
            <Ellipse cx="210" cy="102" rx="65" ry="41" fill="#c79864" />
            <Ellipse cx="210" cy="102" rx="51" ry="29" fill="#e5b77e" />
          </G>
          <G transform="translate(390 335)">
            <Rect
              width="416"
              height="310"
              rx="32"
              fill="none"
              stroke="#31a66d"
              strokeWidth="4"
              strokeDasharray="13 10"
            />
            <Rect x="16" y="-13" width="92" height="34" rx="17" fill="#31a66d" />
            <Text x="62" y="10" fill="white" fontSize="17" fontWeight="700" textAnchor="middle">
              活动区
            </Text>
          </G>
        </G>
      )}

      {/* 房间名标签：真实模板用深色胶囊（压在照片上），卡通模板用灰字 */}
      {mapStyle === 'real' ? (
        <G textAnchor="middle" fontWeight="700">
          {[
            { x: 183, y: 182, label: '次卧' },
            { x: 431, y: 170, label: '卫生间' },
            { x: 700, y: 177, label: '厨房' },
            { x: 190, y: 525, label: '主卧' },
            { x: 610, y: 470, label: '客厅' },
            { x: 610, y: 734, label: '阳台' },
          ].map((item) => (
            <G key={item.label} transform={`translate(${item.x} ${item.y})`}>
              <Rect
                x={item.label.length > 2 ? -43 : -37}
                y={-18}
                width={item.label.length > 2 ? 86 : 74}
                height={34}
                rx={17}
                fill="rgba(33,42,40,0.63)"
                stroke="rgba(255,255,255,0.55)"
                strokeWidth="1"
              />
              <Text y={5} fill="white" fontSize="15">
                {item.label}
              </Text>
            </G>
          ))}
        </G>
      ) : (
        <G fill="#394543" fontSize="22" fontWeight="700" textAnchor="middle">
          <Text x="190" y="188">
            次卧
          </Text>
          <Text x="431" y="170">
            卫生间
          </Text>
          <Text x="685" y="177">
            厨房
          </Text>
          <Text x="192" y="515">
            主卧
          </Text>
          <Text x="605" y="470">
            客厅
          </Text>
          <Text x="606" y="744">
            阳台
          </Text>
        </G>
      )}

      {/* 轨迹（渐隐虚线） */}
      <Polyline
        points={trail.map((point) => `${point.x},${point.y}`).join(' ')}
        fill="none"
        stroke="#ef5b4f"
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.42"
        strokeDasharray="2 18"
      />

      {/* 宠物标记（仅 1F） */}
      {floor === 1 && (
        <G transform={`translate(${position.x} ${position.y}) rotate(${angle})`}>
          <Circle r={pulse ? 60 : 48} fill="url(#markerGlow)" opacity={pulse ? 0.9 : 0.5} />
          {position.behavior === '奔跑' && (
            <Path d="M34 -17 L95 -44 L95 44 L34 17 Z" fill="#ef5b4f" opacity="0.2" />
          )}
          <Circle r="32" fill="#ef5b4f" stroke="white" strokeWidth="6" />
          <G transform={`rotate(${-angle})`}>
            <Text x="0" y="10" textAnchor="middle" fontSize="29">
              {petEmoji}
            </Text>
          </G>
        </G>
      )}
    </Svg>
  );
}
