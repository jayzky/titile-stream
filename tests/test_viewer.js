const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadViewer(fetch) {
  const html = fs.readFileSync(path.join(__dirname, '../src/service/viewer.html'), 'utf8');
  const elements = new Map();
  const context = { clearRect() {},
    createImageData: (width, height) => ({ data: new Uint8ClampedArray(width * height * 4) }),
    putImageData() {} };
  const getElementById = id => {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'source-select' ? 'simulated' : '(B8-B4)/(B8+B4)',
      textContent: '', disabled: false, width: 256, height: 256,
      getContext: () => context,
    });
    return elements.get(id);
  };
  const map = {
    fitBounds: (...args) => { map.lastFit = args; },
    invalidateSize() {}, removeLayer() {}, hasLayer: () => false,
    getZoom: () => 10, getCenter: () => [30.5, 114.4],
    project: () => ({ divideBy: () => ({ floor: () => ({ x: 837, y: 420 }) }) }),
  };
  const layer = { addTo() { return this; }, bindPopup() {}, setBounds() { return this; }, on() {} };
  const sandbox = {
    document: { getElementById }, window: { addEventListener() {} },
    L: { tileLayer: () => layer, map: () => map,
      control: { zoom: () => layer }, rectangle: () => layer, imageOverlay: () => layer },
    setTimeout() {}, fetch, URLSearchParams,
  };
  vm.runInNewContext(html.match(/<script>([\s\S]*)<\/script>/)[1], sandbox);
  return { get: getElementById, map };
}

const metadata = {
  name: 'demo.tif', width: 256, height: 256, crs: 'EPSG:4326',
  bands: [{ name: 'B4' }, { name: 'B8' }], data_kind: 'synthetic_demo',
  bbox: { west: 114.2, south: 30.3, east: 114.6, north: 30.7 },
};
const jsonResponse = (data, ok = true) => ({ ok, json: async () => data });

test('raster selection shows available bands and fits actual coverage', async () => {
  const viewer = loadViewer(async request => {
    assert.equal(request, '/sources/raster');
    return jsonResponse(metadata);
  });
  assert.equal(viewer.get('calc-btn').disabled, false);
  viewer.get('source-select').value = 'raster';
  await viewer.get('source-select').onchange();
  assert.match(viewer.get('source-details').textContent, /B4、B8/);
  assert.match(viewer.get('source-details').textContent, /非卫星影像/);
  assert.equal(JSON.stringify(viewer.map.lastFit[0]), JSON.stringify([[30.3, 114.2], [30.7, 114.6]]));
  assert.equal(viewer.get('calc-btn').disabled, false);
});

test('failed raster selection disables calculations and switching back restores it', async () => {
  const viewer = loadViewer(async () => jsonResponse({ detail: '栅格文件不存在' }, false));
  viewer.get('source-select').value = 'raster';
  await viewer.get('source-select').onchange();
  assert.match(viewer.get('source-details').textContent, /读取失败.*文件不存在/);
  assert.equal(viewer.get('calc-btn').disabled, true);
  viewer.get('source-select').value = 'simulated';
  await viewer.get('source-select').onchange();
  assert.equal(viewer.get('calc-btn').disabled, false);
  assert.match(viewer.get('source-details').textContent, /模拟波段/);
});

test('late raster metadata cannot overwrite a newer source choice', async () => {
  let resolveRequest;
  const viewer = loadViewer(() => new Promise(resolve => { resolveRequest = resolve; }));
  viewer.get('source-select').value = 'raster';
  const previous = viewer.get('source-select').onchange();
  viewer.get('source-select').value = 'simulated';
  await viewer.get('source-select').onchange();
  resolveRequest(jsonResponse(metadata));
  await previous;
  assert.match(viewer.get('source-details').textContent, /模拟波段/);
  assert.equal(viewer.map.lastFit, undefined);
});

test('formula requiring absent raster band reports it before requesting calculation', async () => {
  let requestCount = 0;
  const viewer = loadViewer(async () => { requestCount++; return jsonResponse(metadata); });
  viewer.get('source-select').value = 'raster';
  await viewer.get('source-select').onchange();
  viewer.get('expr-input').value = '(B3-B8)/(B3+B8)';
  await viewer.get('calc-btn').onclick();
  assert.equal(requestCount, 1);
  assert.match(viewer.get('request-status').textContent, /缺少波段 B3/);
  assert.equal(viewer.get('calc-btn').disabled, false);
});

const result = {
  pixels: [[0.4, null]], valid_count: 1, nodata_count: 1,
  stats: { mean: 0.4, max: 0.4, min: 0.4, std: 0 }, source_description: '自建演示数据',
};

test('calculation shows backend statistics and result preview', async () => {
  const viewer = loadViewer(async request => jsonResponse(
    request.includes('/calc?') ? result : { bbox: metadata.bbox }));
  await viewer.get('calc-btn').onclick();
  assert.equal(viewer.get('stat-mean').textContent, '0.400000');
  assert.equal(viewer.get('stat-count').textContent, '1 / 1');
  assert.match(viewer.get('request-status').textContent, /计算完成.*自建演示数据/);
});

test('late calculation cannot display a previous source result after switching', async () => {
  let resolveResult;
  const viewer = loadViewer(request => request.includes('/calc?')
    ? new Promise(resolve => { resolveResult = resolve; })
    : Promise.resolve(jsonResponse({ bbox: metadata.bbox })));
  const calculation = viewer.get('calc-btn').onclick();
  await viewer.get('source-select').onchange();
  resolveResult(jsonResponse(result));
  await calculation;
  assert.equal(viewer.get('stat-mean').textContent, '—');
  assert.match(viewer.get('request-status').textContent, /已选择模拟数据/);
});
