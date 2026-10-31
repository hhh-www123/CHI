// Integration check with live AMap, on an isolated data directory and port.
// UI_BASE_URL must identify the isolated server. No model requests are made.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { openLegacyTrip, resumeSavedTrip } = require('./browser-session.cjs');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(process.env.PLAYWRIGHT_MODULE_DIR || path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8001';
const artifacts = process.env.UI_TEST_ARTIFACT_DIR || path.join(__dirname, 'artifacts');
const executablePath = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(p => fs.existsSync(p));
(async () => {
 const browser = await playwright.chromium.launch({headless:true,executablePath});
 const context = await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN',reducedMotion:'reduce'});
 const page = await context.newPage();
 page.setDefaultTimeout(20000);
 const errors = [], results = [];
 let generations=0;
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(new URL(request.url()).pathname.endsWith('/generate')) generations++;});
 const responseFor = async (method,suffix,callback) => {
  const [response] = await Promise.all([page.waitForResponse(r=>r.request().method()===method && new URL(r.url()).pathname.endsWith(suffix),{timeout:120000}),callback()]);
  const body=await response.json(); assert.equal(response.status(),200,JSON.stringify(body));return body;
 };
 const read = async () => {
  const id=await page.evaluate(()=>localStorage.getItem('travel-genui-session-v1'));
  const response=await context.request.get(base+'/api/sessions/'+id);assert.equal(response.status(),200);return response.json();
 };
 const mapLoaded = async () => {
  const image=page.locator('.route-map-figure img');
  await image.waitFor();
  await page.waitForFunction(()=>{const img=document.querySelector('.route-map-figure img');return img && img.complete && img.naturalWidth>0;},{},{timeout:20000});
 };
 try {
  await openLegacyTrip(page, context, base);await page.getByRole('tabpanel').waitFor();
  const catalog=await (await context.request.get(base+'/api/catalog')).json();
  assert.equal(catalog.places.length,15);assert.equal(catalog.hotels.length,3);
  assert.equal(await page.locator('select[data-testid^="alternative-"]').count(),3);
  const first=page.getByTestId('alternative-day2-stop-1');
  assert.equal(await first.locator('option').count(),13);
  assert.equal(await first.locator('option[value="bamboo-path"]').count(),0);
  assert.equal(await first.locator('option[value="tea-house"]').count(),0);
  assert.ok(await page.getByRole('heading',{name:'龙井村（牌坊）',exact:true}).isVisible());
  assert.equal(await page.getByLabel('选择酒店',{exact:true}).locator('option').count(),3);
  results.push('15 real places and 3 real hotels; per-stop grouped dropdown excludes same-day duplicates');
  const note='实测：下午留出休息时间，换景点后保留这条备注。';
  await page.getByLabel('第二天行程备注',{exact:true}).fill(note);
  await responseFor('PATCH','/direct',()=>page.getByLabel('第二天行程备注',{exact:true}).press('Tab'));
  const baseline=await read();
  let current=await responseFor('PATCH','/direct',()=>first.selectOption('city-museum'));
  assert.equal(current.tripState.days.day2.stops[0].placeId,'city-museum');
  assert.equal(current.tripState.days.day2.stops[0].id,baseline.tripState.days.day2.stops[0].id);
  assert.equal(current.tripState.days.day2.stops[0].startTime,baseline.tripState.days.day2.stops[0].startTime);
  assert.equal(current.tripState.days.day2.note,note);
  assert.deepEqual(current.tripState.days.day1,baseline.tripState.days.day1);
  assert.equal(current.tripState.hotelId,baseline.tripState.hotelId);
  results.push('Direct dropdown replacement preserves stable stop ID, time, notes, other dates and hotel');
  current=await responseFor('POST','/routes',()=>page.getByTestId('refresh-day-routes').click());
  assert.equal(current.routeData['day-2'].status,'ready');
  assert.ok(current.routeData['day-2'].points.every(p=>p.status==='resolved'));
  assert.ok(current.routeData['day-2'].segments.every(s=>s.status==='ready' && s.durationMinutes>0));
  await mapLoaded();
  fs.mkdirSync(artifacts,{recursive:true});
  await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:path.join(artifacts,'real-places-desktop.png'),fullPage:true});
  results.push('Map button queries live AMap and loads an actual PNG with real route distances and durations');
  const routeBefore=current.routeData['day-2'];
  current=await responseFor('PATCH','/direct',()=>page.getByTestId('alternative-day2-stop-1').selectOption('silk-museum'));
  assert.equal(current.tripState.days.day2.note,note);
  assert.equal(current.routeData['day-2'].points[0].name,'中国丝绸博物馆');
  assert.notEqual(current.routeData['day-2'].points[0].poiId,routeBefore.points[0].poiId);
  assert.ok(current.routeData['day-2'].points.every(p=>p.status==='resolved'));
  await mapLoaded();
  current=await responseFor('POST','/undo',()=>page.getByRole('button',{name:'撤销上一步',exact:true}).click());
  assert.equal(current.tripState.days.day2.stops[0].placeId,'city-museum');
  assert.equal(current.routeData['day-2'].points[0].name,'浙江省博物馆（孤山馆区）');
  assert.equal(current.tripState.days.day2.note,note);
  results.push('Visible route refreshes automatically after replacement; undo restores both previous place and previous route');
  current=await responseFor('PATCH','/direct',()=>page.getByLabel('选择酒店',{exact:true}).selectOption('canal-hotel'));
  assert.equal(current.tripState.hotelId,'canal-hotel');
  assert.ok(await page.getByText('住宿预算估算',{exact:true}).isVisible());
  assert.ok(await page.locator('.hotel-address').isVisible());
  results.push('Real hotel dropdown updates selection and exposes an address and a budget-only price label');
  current=await responseFor('POST','/routes',()=>page.getByTestId('refresh-all-routes').click());
  for (const region of ['day-1','day-2','day-3']) {
   assert.equal(current.routeData[region].status,'ready',region+': '+current.routeData[region].message);
   assert.ok(current.routeData[region].points.every(p=>p.status==='resolved'));
   assert.ok(current.routeData[region].segments.every(s=>s.status==='ready'));
  }
  await page.getByRole('tab',{name:/第三天/}).click();await mapLoaded();
  assert.ok(current.routeData['day-3'].segments.some(s=>s.mode==='walking'));
  results.push('All three dates can query real routes; short canal-area hops transparently fall back to real walking directions');
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
  const width=await page.evaluate(()=>({viewport:innerWidth,content:document.documentElement.scrollWidth}));
  if(width.content>width.viewport+1){const offenders=await page.evaluate(()=>Array.from(document.querySelectorAll('body *')).map(e=>({tag:e.tagName,class:String(e.className),left:Math.round(e.getBoundingClientRect().left),right:Math.round(e.getBoundingClientRect().right)})).filter(e=>e.left<0||e.right>innerWidth+1));throw new Error(JSON.stringify({width,offenders:offenders.slice(0,20)}));}
  await page.screenshot({path:path.join(artifacts,'real-places-mobile.png'),fullPage:true});
  assert.equal(generations,0);assert.deepEqual(errors,[]);
  results.push('390px layout and runtime pass; dropdown and route actions made zero model requests');
  const report={passed:true,liveMap:true,results};
  fs.writeFileSync(path.join(artifacts,'real-places-results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 } catch(error) {
  fs.mkdirSync(artifacts,{recursive:true});await page.screenshot({path:path.join(artifacts,'real-places-failure.png'),fullPage:true}).catch(()=>{});throw error;
 } finally {await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
