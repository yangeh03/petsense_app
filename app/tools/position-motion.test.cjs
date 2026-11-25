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
    createAnimatedComponent: () => 'marker',
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
    useAnimatedProps: (fn) => fn(),
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
        if (name === 'react-native-svg') return { Circle: 'circle' };
        if (name === '@/services/position-motion') return out;
        throw new Error(name);
      },
    },
  );
  const render = (point, fresh = true, remainingMs = 2000) => {
    cursor = 0;
    pending = [];
    const tree = component.PositionMarker({ point, polygon: square, fresh, remainingMs });
    pending.forEach((fn) => fn());
    return tree;
  };
  render({ x: 1, y: 1 });
  assert.equal(animations.length, 0);
  render({ x: 2, y: 1 });
  assert.equal(animations.length, 1);
  location.value = { x: 1.4, y: 1 };
  render({ x: 3, y: 2 });
  assert.equal(animations[1].from.x, 1.4);
  render({ x: 3, y: 2 }, true, 1000);
  assert.equal(animations.length, 2);
  const stale = render({ x: 3, y: 2 }, false, 0);
  assert.equal(stale.props.opacity, 0.4);
  assert.equal(location.value.x, 1.4);
  render({ x: 2, y: 3 });
  assert.equal(animations.length, 2);
  assert.equal(location.value.y, 3);
});
