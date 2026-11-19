const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { runInSandbox } = require('./vm-sandbox.cjs')
const ts = require('typescript')

const root = path.join(__dirname, '..')
function load() {
  const file = path.join(root, 'src/features/studio/StreamModePanel.tsx')
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  runInSandbox(code, {
    exports,
    require: name => {
      if (name === '../../i18n') return { t: s => s }
      if (name.endsWith('/jsx-runtime')) return { jsx: () => null, jsxs: () => null, Fragment: 'Fragment' }
      return {}
    },
  })
  return exports
}
const panel = load()
const plain = value => JSON.parse(JSON.stringify(value)) // the sandbox is another realm: compare structure, not prototypes

test('clip count is clamped to 1..20 and falls back to the default', () => {
  assert.equal(panel.clampStreamTop('5'), 5)
  assert.equal(panel.clampStreamTop('0'), 1)
  assert.equal(panel.clampStreamTop('99'), 20)
  assert.equal(panel.clampStreamTop('5.9'), 5)
  assert.equal(panel.clampStreamTop(''), panel.STREAM_TOP_DEFAULT)
  assert.equal(panel.clampStreamTop('abc'), panel.STREAM_TOP_DEFAULT)
  assert.equal(panel.STREAM_TOP_DEFAULT, 8)
})

test('form fields are sent only in stream mode, with safe defaults', () => {
  const fields = options => plain(panel.streamFormFields(options))
  assert.deepEqual(fields({}), [])
  assert.deepEqual(fields({ stream: false, stream_top: 5 }), [])
  assert.deepEqual(fields({ stream: true }), [['stream', 'true'], ['stream_top', '8'], ['stream_layout', 'auto']])
  assert.deepEqual(fields({ stream: true, stream_top: 50, stream_layout: 'stack' }), [['stream', 'true'], ['stream_top', '20'], ['stream_layout', 'stack']])
  assert.deepEqual(fields({ stream: true, stream_layout: 'bogus' }).at(-1), ['stream_layout', 'auto'])
})

test('a stream never waits for a transcript or the first-30-minutes tip', () => {
  assert.equal(panel.transcriptNotRequired({ stream: true }, false), true)
  assert.equal(panel.transcriptNotRequired({ stream: false }, false), false)
  assert.equal(panel.transcriptNotRequired({}, true), true)
})

test('the import page wires the panel, chat upload and gates', () => {
  const src = fs.readFileSync(path.join(root, 'src/features/studio/CreativeImport.tsx'), 'utf8')
  assert.match(src, /streamFormFields\(options\)/)
  assert.match(src, /body\.append\('chat', chatRef\.current\)/)
  assert.match(src, /<StreamModePanel/)
  assert.match(src, /transcriptNotRequired\(options, !!subtitle\)/)
  assert.match(src, /!options\.stream && shouldSuggestFirstWindow/)
})

test('every new string exists in all 8 locales', () => {
  const src = fs.readFileSync(path.join(root, 'src/features/studio/StreamModePanel.tsx'), 'utf8')
  const keys = [...src.matchAll(/\bt\(\s*["']([^"']+)["']/g)].map(m => m[1])
  assert.ok(keys.length >= 6)
  for (const loc of ['zh', 'en', 'es', 'fr', 'ja', 'ko', 'pt', 'ru']) {
    const json = JSON.parse(fs.readFileSync(path.join(root, `src/i18n/locales/${loc}.json`), 'utf8'))
    for (const key of keys) assert.ok(key in json, `${loc} is missing: ${key}`)
  }
})
