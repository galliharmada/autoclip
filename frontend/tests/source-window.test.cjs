const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { runInSandbox } = require('./vm-sandbox.cjs')
const ts = require('typescript')

// The picker is one file with pure helpers next to its render: load it with the
// i18n/React/ui modules stubbed so the helpers can be exercised without a DOM.
function load() {
  const file = path.join(__dirname, '../src/features/studio/SourceWindowPicker.tsx')
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  runInSandbox(code, {
    exports,
    require: name => {
      if (name === '../../i18n') return { t: (s, vars) => (vars ? `${s}:${JSON.stringify(vars)}` : s) }
      if (name === '../../ui') return { Btn: () => null }
      if (name === 'react') return { useEffect: () => undefined, useRef: v => ({ current: v }), useState: v => [v, () => undefined] }
      if (name.endsWith('/jsx-runtime')) return { jsx: () => null, jsxs: () => null, Fragment: 'Fragment' }
      return {}
    },
  })
  return exports
}

const picker = load()

test('time points accept plain seconds, m:ss and h:mm:ss', () => {
  assert.equal(picker.parseTimePoint('90'), 90)
  assert.equal(picker.parseTimePoint('1:30'), 90)
  assert.equal(picker.parseTimePoint('01:30'), 90)
  assert.equal(picker.parseTimePoint('1:02:03'), 3723)
  assert.equal(picker.parseTimePoint(' 2:30 '), 150)
  assert.equal(picker.parseTimePoint('2：30'), 150) // full-width colon from a Chinese keyboard
})

test('a half typed or meaningless time point stays unparsed instead of becoming 0', () => {
  for (const text of ['', '   ', 'abc', '1:xx', '-5', '1:2:3:4', ':']) {
    assert.equal(picker.parseTimePoint(text), null, text)
  }
})

test('formatting round-trips and never shows a negative clock', () => {
  assert.equal(picker.formatTimePoint(0), '0:00')
  assert.equal(picker.formatTimePoint(90), '1:30')
  assert.equal(picker.formatTimePoint(3723), '1:02:03')
  assert.equal(picker.formatTimePoint(-1), '')
  assert.equal(picker.formatTimePoint(null), '')
  for (const seconds of [0, 7, 61, 599, 3600, 3723]) {
    assert.equal(picker.parseTimePoint(picker.formatTimePoint(seconds)), seconds)
  }
})

/** The sandbox has its own Object prototype: re-create plain objects before deep equality. */
const plain = value => (value == null ? value : { ...value })

test('a window is clamped onto the real source and must contain at least a second', () => {
  assert.deepEqual(plain(picker.normalizeWindow(60, 300, 600)), { start: 60, end: 300 })
  assert.deepEqual(plain(picker.normalizeWindow(-10, 300, 600)), { start: 0, end: 300 })
  assert.deepEqual(plain(picker.normalizeWindow(60, 900, 600)), { start: 60, end: 600 })
  assert.equal(picker.normalizeWindow(300, 300, 600), null)
  assert.equal(picker.normalizeWindow(400, 350, 600), null)
  assert.equal(picker.normalizeWindow(null, 300, 600), null)
})

test('the estimate follows the chosen range, not the whole file', () => {
  assert.equal(picker.effectiveDuration({ start: 300, end: 480 }, 3600), 180)
  assert.equal(picker.effectiveDuration(null, 3600), 3600)
  assert.equal(picker.effectiveDuration({ start: 10, end: 10 }, 3600), 3600) // degenerate range is ignored
})

test('the picker explains what is wrong instead of silently ignoring it', () => {
  assert.equal(picker.windowProblem(null, 600, '', ''), null)
  assert.match(picker.windowProblem(null, 600, 'abc', '3:00'), /分:秒/)
  assert.match(picker.windowProblem({ start: 0, end: 300 }, 600, '0:00', '99:00'), /超出视频长度/)
  assert.match(picker.windowProblem(null, 600, '5:00', '0:30'), /晚/)
  assert.equal(picker.windowProblem({ start: 60, end: 300 }, 600, '1:00', '5:00'), null)
})

test('a window whose end is inside the file passes even without a known duration', () => {
  assert.equal(picker.windowProblem({ start: 60, end: 300 }, null, '1:00', '5:00'), null)
  assert.equal(picker.MIN_WINDOW_SEC, 1)
})