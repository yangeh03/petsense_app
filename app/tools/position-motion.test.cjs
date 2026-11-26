const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const { test } = require('node:test');
const out = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(
      require('node:path').join(__dirname, '../src/services/position-motion.ts'),
      'utf8',
    ),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } },
  ).outputText,
  { exports: out },
);
const square = [
  [0, 0],
  [4, 0],
  [4, 4],
  [0, 4],
];
const notch = [
  [0, 0],
  [4, 0],
  [4, 4],
  [3, 4],
  [3, 1],
  [1, 1],
  [1, 4],
  [0, 4],
];
test('animation target is bounded, including inside endpoints across a concave notch', () => {
  const stop = out.motionTarget({ x: 0.5, y: 3 }, { x: 3.5, y: 3 }, notch);
  assert.equal(stop.x, 1);
  assert.equal(stop.y, 3);
  assert.equal(out.motionTarget(null, { x: 5, y: 2 }, square).x, 4);
  for (let i = 0; i <= 100; i++)
    assert.ok(out.insideBoundary({ x: 0.5 + ((stop.x - 0.5) * i) / 100, y: 3 }, notch));
});
test('motion follows metres without overshoot and cannot outlive measurement freshness', () => {
  const target = { x: 2, y: 3 };
  assert.equal(out.motionTarget({ x: 1, y: 1 }, target, square), target);
  assert.equal(out.motionDuration(2000), 850);
  assert.equal(out.motionDuration(150), 150);
  assert.equal(out.motionDuration(0), 0);
});

test('marker retargets from its displayed position, ignores duplicate polls, freezes stale and snaps on recovery', () => {
  const slots = [];
  let cursor = 0;
  let pending = [];
  const animations = [];
  let location;
  const slot = (create) => {
    const i = cursor++;
    return slots[i] ?? (slots[i] = create());
  };
  const react = {
    useState: (value) => [slot(() => value), () => {}],
    useRef: (value) => slot(() => ({ current: value })),
    useEffect: (fn, deps) => {
      const state = slot(() => ({ deps: null }));
      if (!state.deps || deps.some((v, i) => v !== state.deps[i])) {
        state.deps = deps;
        pending.push(fn);
      }
    },
  };
  const animated = {
    View: 'marker',
    useReducedMotion: () => false,
    useSharedValue: (initial) =>
      slot(() => {
        location = {
          value: initial,
          get() {
            return this.value;
          },
          set(next) {
            if (next.animation) animations.push({ from: this.value, to: next.target });
            else this.value = next;
          },
        };
        return location;
      }),
    useAnimatedStyle: (fn) => fn(),
    cancelAnimation: () => {},
    Easing: { linear: 'linear' },
    withTiming: (target) => ({ animation: true, target }),
  };
  const component = {};
  vm.runInNewContext(
    ts.transpileModule(
      fs.readFileSync(
        require('node:path').join(__dirname, '../src/components/location/position-marker.tsx'),
        'utf8',
      ),
      {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          jsx: ts.JsxEmit.ReactJSX,
          esModuleInterop: true,
        },
      },
    ).outputText,
    {
      exports: component,
      require: (name) => {
        if (name === 'react') return react;
        if (name === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }) };
        if (name === 'react-native-reanimated')
          return { ...animated, default: animated, __esModule: true };
        if (name === 'expo-image') return { Image: 'image' };
        if (name === 'react-native') return { StyleSheet: { create: (x) => x, absoluteFill: {} } };
        if (name === '@/components/themed-text') return { ThemedText: 'text' };
        if (name === '@/hooks/use-theme')
          return {
            useTheme: () => ({ tint: '#E8835C', textSecondary: '#666', backgroundElement: '#fff' }),
          };
        if (name === '@/services/api') return { absoluteUrl: (url) => url };
        if (name === '@/services/position-motion') return out;
        throw new Error(name);
      },
    },
  );
  const render = (point, fresh = true, remainingMs = 2000, pet) => {
    cursor = 0;
    pending = [];
    const tree = component.PositionMarker({
      point,
      polygon: square,
      fresh,
      remainingMs,
      bounds: { minX: 0, minY: 0, width: 4, height: 4 },
      viewport: { width: 400, height: 400 },
      zoom: 1,
      pet,
    });
    pending.forEach((fn) => fn());
    return tree;
  };
  const first = render({ x: 1, y: 1 });
  assert.equal(first.props.style[1].width, 40);
  assert.equal(first.props.style[1].borderRadius, 20);
  assert.equal(first.props.style[0].overflow, 'hidden');
  assert.equal(first.props.style[2].transform[0].translateX, 80);
  assert.equal(first.props.style[2].transform[1].translateY, 280);
  assert.equal(animations.length, 0);
  render({ x: 2, y: 1 });
  assert.equal(animations.length, 1);
  location.value = { x: 1.4, y: 1 };
  render({ x: 3, y: 2 });
  assert.equal(animations[1].from.x, 1.4);
  render({ x: 3, y: 2 }, true, 1000);
  assert.equal(animations.length, 2);
  const stale = render({ x: 3, y: 2 }, false, 0);
  assert.equal(stale.props.style[1].opacity, 0.45);
  assert.equal(location.value.x, 1.4);
  render({ x: 2, y: 3 });
  assert.equal(animations.length, 2);
  assert.equal(location.value.y, 3);
  const walking = render({ x: 2, y: 3 }, true, 2000, {
    name: 'Marni',
    label: '走动',
    current: true,
    animationUrl: '/walk.gif',
  });
  assert.equal(walking.props.children.type, 'image');
  assert.equal(walking.props.children.props.source.uri, '/walk.gif');
  const sitting = render({ x: 2, y: 3 }, true, 2000, {
    name: 'Marni',
    label: '坐着',
    current: true,
    animationUrl: '/sit.gif',
  });
  assert.equal(sitting.props.children.props.source.uri, '/sit.gif');
  assert.equal(animations.length, 2, 'changing action must not restart location interpolation');
});
