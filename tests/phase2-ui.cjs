const previewResponse=require('./preview-response.cjs');
// Phase 2 browser acceptance. Intentionally restricted to the isolated demo server.
// Start 8013 with empty model/map keys, CANDIDATE_AGENT_MODE=mock and
// CANDIDATE_TTL_SECONDS=30. Never targets the user's production database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(process.env.PLAYWRIGHT_MODULE_DIR || path.join(process.env.USERPROFILE || '', '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const root = path.resolve(__dirname, '..');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8013';
assert.equal(base, 'http://127.0.0.1:8013', 'Only the isolated browser test server is permitted');
const database = path.join(root, '.runtime/phase1-browser/travel.sqlite3');
const python = process.env.UI_PYTHON_PATH || path.join(root, '.venv/Scripts/python.exe');
const artifactPrefix = process.env.UI_ARTIFACT_PREFIX || 'phase2';
assert.match(artifactPrefix, /^[a-z0-9-]{1,64}$/);
const artifact = name => name.replace(/^phase2-/, artifactPrefix + '-');
const shots = path.join(root, 'docs/screenshots');
const reports = path.join(root, 'docs/verification');
fs.mkdirSync(shots, { recursive: true }); fs.mkdirSync(reports, { recursive: true });
const executablePath = [process.env.UI_BROWSER_PATH, 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].filter(Boolean).find(value => fs.existsSync(value));
function stored(id) {
  const script = 'import sqlite3,sys; from pathlib import Path; c=sqlite3.connect(Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True); r=c.execute("SELECT document FROM sessions WHERE id=?",(sys.argv[2],)).fetchone(); assert r; print(r[0]); c.close()';
  const result = spawnSync(python, ['-X','utf8','-c',script,database,id], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr); return result.stdout.trim();
}
function leafDiff(a, b, prefix = '') {
  if (JSON.stringify(a) === JSON.stringify(b)) return [];
  if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) {
    return [...new Set([...Object.keys(a), ...Object.keys(b)])].sort().flatMap(key => leafDiff(a[key], b[key], prefix + '/' + key.replace(/~/g,'~0').replace(/\//g,'~1')));
  }
  return [prefix];
}
(async () => {
  const browser = await playwright.chromium.launch({ headless:true, ...(executablePath ? { executablePath } : {}) });
  const context = await browser.newContext({ viewport: { width:1600,height:1100 }, locale:'zh-CN', reducedMotion:'reduce' });
  const page = await context.newPage(); page.setDefaultTimeout(18000);
  const errors = [], results = [], networkEffects = [], outbound = [];
  let tripId = '';
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (!['127.0.0.1','localhost'].includes(url.hostname)) outbound.push(url.origin + url.pathname);
    if (['POST','PUT','PATCH','DELETE'].includes(request.method())) networkEffects.push(request.method()+' '+url.pathname);
  });
  const record = name => { results.push({ name, passed:true }); console.log('PASS ' + name); };
  const current = async () => { const response = await context.request.get(base+'/api/sessions/'+tripId); assert.equal(response.status(),200); return response.json(); };
  const post = async (suffix, data, status=200) => {
    const response = await context.request.post(base+'/api/sessions/'+tripId+suffix, { data });
    const body = await previewResponse(response); assert.equal(body.__streamStatus??response.status(),status,JSON.stringify(body)); return body;
  };
  async function action(method, suffix, callback, status=200) {
    const waiting = page.waitForResponse(response => response.request().method()===method && new URL(response.url()).pathname.endsWith(suffix));
    await callback(); const response = await waiting; const body = await previewResponse(response);
    assert.equal(body.__streamStatus??response.status(),status,JSON.stringify(body)); await page.waitForTimeout(100); return body;
  }
  async function unchanged(session, row, reason) {
    assert.deepEqual(await current(),session,reason+' (public GET)'); assert.equal(stored(tripId),row,reason+' (SQLite bytes)');
  }
  const panel = () => page.getByTestId('phase2-editor');
  const originalCards = () => page.locator('.itinerary-column .stops-list .stop-card');
  const metrics = () => page.evaluate(() => ({ left:document.querySelector('.itinerary-column').scrollTop,right:document.querySelector('.control-panel').scrollTop,window:window.scrollY,width:document.documentElement.scrollWidth,viewport:innerWidth }));
  async function desktopScreenshot(filename, stopId) {
    while (await page.getByRole('button',{name:'关闭提示',exact:true}).count()) await page.getByRole('button',{name:'关闭提示',exact:true}).first().click();
    await page.evaluate(stopId => {
      const left=document.querySelector('.itinerary-column'), right=document.querySelector('.control-panel');
      const card=left.querySelector('[data-stop-id="'+stopId+'"]');
      left.scrollTop += card.getBoundingClientRect().top-left.getBoundingClientRect().top-90;
      const candidates=right.querySelector('[data-testid="phase2-candidates"]');
      right.scrollTop += candidates.getBoundingClientRect().top-right.getBoundingClientRect().top-12;
    },stopId);
    await page.waitForTimeout(100);
    await page.screenshot({path:path.join(shots,artifact(filename))});
  }

  try {
    const status = await (await context.request.get(base+'/api/agent/status')).json();
    const editsStatus = await (await context.request.get(base+'/api/edits/status')).json();
    assert.equal(status.mode,'demo'); assert.equal(status.mapConfigured,false); assert.equal(status.runtimeCodeExecution,false);
    assert.ok(['Phase 2','Phase 3','Phase 4'].includes(editsStatus.phase)); assert.equal(editsStatus.defaultMode,'mock'); assert.equal(editsStatus.llmConfigured,false);
    assert.equal(editsStatus.candidateTtlSeconds,30,'Use the dedicated 30-second TTL fixture for a real expiry test');
    assert.ok(fs.existsSync(database));
    await page.goto(base,{waitUntil:'networkidle'});
    let session = await action('POST','/api/demo-sessions',()=>page.getByTestId('open-three-day-demo').click()); tripId=session.id;
    await page.getByTestId('browse-agent-dialog').waitFor(); assert.equal(await originalCards().count(),3); assert.equal(await page.locator('iframe').count(),0);
    const baseline = await current(), baselineRow = stored(tripId);
    assert.equal(await page.getByTestId('local-editor-panel').count(),0);
    assert.equal(await page.getByTestId('global-controls').count(),0);
    await page.getByLabel('告诉 Agent 如何修改当前日期').fill('安排轻松一些');
    const browseRequestsBefore=networkEffects.filter(item=>item==='POST /api/sessions/'+tripId+'/edits/candidates').length;
    await page.getByTestId('browse-agent-submit').click();
    const serviceDialog=page.getByRole('dialog',{name:'服务接入',exact:true}); await serviceDialog.waitFor();
    assert.ok(await serviceDialog.getByTestId('model-integration-form').evaluate(node=>node.classList.contains('is-target')));
    assert.match(await serviceDialog.getByTestId('model-integration-form').innerText(),/模型 API|未接入/);
    assert.equal(networkEffects.filter(item=>item==='POST /api/sessions/'+tripId+'/edits/candidates').length,browseRequestsBefore);
    await unchanged(baseline,baselineRow,'Browse-mode Agent without an API must open service settings and remain read-only');
    await serviceDialog.getByRole('button',{name:'关闭服务接入',exact:true}).click();
    const noSelection = await post('/edits/candidates',{baseVersion:baseline.version,selectedComponentIds:[],instruction:'安排轻松一些',agentMode:'mock'},400);
    assert.equal(noSelection.detail.code,'NO_SELECTION'); await unchanged(baseline,baselineRow,'No-selection failure must not alter state');
    record('Browse mode shows only the current-day Agent dialog; a missing model API opens service settings and empty API selection is rejected');

    await page.getByTestId('edit-mode-toggle').click();
    await panel().waitFor();
    const registry=Object.values(session.componentRegistry);
    const day=key=>registry.find(item=>item.dayKey===key&&!item.stopId&&!item.period);
    const pm=registry.find(item=>item.dayKey==='day2'&&item.period==='pm'&&!item.stopId);
    assert.ok(pm);
    session=await action('PATCH','/editor/locks',()=>page.getByTestId('local-editor-panel').getByRole('button',{name:'锁定'+day('day1').label,exact:true}).click());
    session=await action('PATCH','/editor/locks',()=>page.getByTestId('local-editor-panel').getByRole('button',{name:'锁定'+day('day3').label,exact:true}).click());
    await page.getByRole('tab',{name:/第二天/}).click(); await page.getByTestId('editor-period-pm').click();
    await page.locator('#editor-value').waitFor();
    assert.ok(session.persistedConstraints.lockedComponentIds.includes(day('day1').componentId));
    assert.ok(session.persistedConstraints.lockedComponentIds.includes(day('day3').componentId));
    const authority=await current(), authorityRow=stored(tripId);
    const mapMarkup=await page.locator('.itinerary-column .route-map-section').allInnerTexts();
    const cardMarkup=await originalCards().allInnerTexts();
    await page.locator('#instruction').fill('安排轻松一些');
    let batch=await action('POST','/edits/candidates',()=>page.getByTestId('generate-edit-candidates').click());
    assert.equal(batch.generationMode,'mock'); assert.equal(batch.baseVersion,authority.version); assert.equal(batch.candidateCount,3);
    assert.deepEqual(batch.candidates.map(item=>item.strategy),['minimal_change','efficiency_first','experience_first']);
    assert.equal(new Set(batch.candidates.map(item=>JSON.stringify(item.patch))).size,3,'Three different structured patches');
    const times=item=>JSON.stringify(Object.fromEntries(batch.effectiveScope.eligibleActivityIds.map(id=>[id,item.document.entities.activities[id].startTime])));
    assert.equal(new Set(batch.candidates.map(times)).size,3,'Different actual time vectors, not only prose');
    assert.equal(batch.effectiveScope.selectedComponentIds[0],pm.componentId);
    for(const candidate of batch.candidates){
      assert.equal(candidate.baseVersion,authority.version); assert.equal(candidate.validationReport.valid,true);
      const actual=leafDiff(authority.tripDocument,candidate.document).sort(); assert.deepEqual(actual,[...candidate.changedPaths].sort());
      assert.ok(actual.every(path=>batch.effectiveScope.allowedPaths.includes(path)));
      assert.ok(Object.values(candidate.validationReport).every(value=>value===true));
    }
    await page.getByTestId('phase2-candidates').waitFor();
    assert.match(await page.getByTestId('phase2-candidates').innerText(),/3\s*\/\s*3/);
    await unchanged(authority,authorityRow,'Generation is preview-only');
    record('Natural-language relaxation request produces three validated mock patches with distinct actual time schedules');

    const switchBaseline=networkEffects.length;
    for(const [index,letter] of ['A','B','C'].entries()){
      const candidate=batch.candidates[index];
      await page.getByTestId('phase2-candidate-'+letter).click();
      const preview=page.getByTestId('phase2-candidate-preview');
      assert.equal(await preview.getAttribute('data-candidate-id'),candidate.candidateId);
      assert.equal(await preview.getAttribute('data-strategy'),candidate.strategy);
      for(const aid of batch.effectiveScope.eligibleActivityIds){
        const card=preview.locator('[data-activity-id="'+aid+'"]');
        assert.equal(await card.locator('.preview-stop-time').innerText(),candidate.document.entities.activities[aid].startTime);
        assert.equal(await card.locator('.stop-description').innerText(),candidate.document.entities.activities[aid].description);
      }
      assert.equal(await preview.locator('a,input,select,textarea,iframe,img,script').count(),0,'Preview has no controls, external links or effectful media');
      assert.deepEqual(await originalCards().allInnerTexts(),cardMarkup,'Original cards are unchanged');
      assert.deepEqual(await page.locator('.itinerary-column .route-map-section').allInnerTexts(),mapMarkup,'Map UI is unchanged');
      await unchanged(authority,authorityRow,'Switching to '+letter+' is read-only');
      await desktopScreenshot('phase2-preview-'+letter.toLowerCase()+'.png',registry.find(item=>item.entityId===batch.effectiveScope.eligibleActivityIds[0]&&item.stopId).stopId);
    }
    assert.equal(networkEffects.length,switchBaseline,'Switching candidates makes no mutation requests');
    record('A → B → C renders shared original card content with isolated readonly data; GET, SQLite, locks and map state remain identical');

    const target=registry.find(item=>item.dayKey==='day1'&&item.stopId);
    const forged=await post('/edits/commit',{baseVersion:authority.version,candidateId:batch.candidates[1].candidateId,patch:[{op:'replace',path:'/entities/activities/'+target.entityId+'/description',value:'forged outside day 2'}],allowedPaths:['/'],lockedIds:[]},422);
    assert.ok(forged.detail); await unchanged(authority,authorityRow,'Client Patch/permissions must be rejected at commit');
    const malicious=await post('/editor/preview',{baseVersion:authority.version,selectedComponentId:pm.componentId,fields:['description'],patch:[{op:'replace',path:'/entities/activities/'+target.entityId+'/description',value:'forged outside day 2'}]},423);
    assert.ok(malicious.detail); await unchanged(authority,authorityRow,'Locked protected day cannot enter the Patch loop');
    record('Forged commit Patch/permissions and out-of-scope protected-day writes are rejected without partial updates');

    await page.getByTestId('phase2-candidate-B').click();
    await desktopScreenshot('phase2-desktop.png',registry.find(item=>item.entityId===batch.effectiveScope.eligibleActivityIds[0]&&item.stopId).stopId);
    await page.screenshot({path:path.join(shots,artifact('phase2-candidates.png'))});
    const candidateB=batch.candidates.find(item=>item.strategy==='efficiency_first');
    const committed=await action('POST','/edits/commit',()=>page.getByTestId('commit-edit-candidate').click());
    assert.equal(committed.version,authority.version+1);
    assert.deepEqual(committed.tripDocument,{...candidateB.document,version:authority.version+1},'Only server-held B document is committed');
    assert.deepEqual(committed.persistedConstraints,authority.persistedConstraints);
    assert.deepEqual(committed.tripState.days.day1,authority.tripState.days.day1); assert.deepEqual(committed.tripState.days.day3,authority.tripState.days.day3);
    assert.deepEqual(committed.uiSpec,authority.uiSpec);
    assert.deepEqual(leafDiff(authority.tripDocument,committed.tripDocument).sort(),[...candidateB.changedPaths,'/version'].sort());
    assert.equal(committed.editorAudit.candidateId,candidateB.candidateId); assert.equal(committed.editorAudit.strategy,'efficiency_first');
    record('Adopting B commits exactly its authorized leaves once, version +1; A/C and day 1/day 3 have no side effects');

    const postCommit=await current(), postCommitRow=stored(tripId);
    await post('/edits/commit',{baseVersion:authority.version,candidateId:batch.candidates[0].candidateId},409);
    await post('/edits/commit',{baseVersion:postCommit.version,candidateId:batch.candidates[2].candidateId},410);
    await unchanged(postCommit,postCommitRow,'Stale version and discarded candidates cannot apply');
    await page.locator('#candidate-agent-mode').selectOption('llm');
    const llmRequestsBefore=networkEffects.filter(item=>item==='POST /api/sessions/'+tripId+'/edits/candidates').length;
    await page.getByTestId('generate-edit-candidates').click();
    const llmDialog=page.getByRole('dialog',{name:'服务接入',exact:true}); await llmDialog.waitFor();
    assert.match(await llmDialog.getByTestId('model-integration-form').innerText(),/模型 API|未接入/);
    assert.equal(networkEffects.filter(item=>item==='POST /api/sessions/'+tripId+'/edits/candidates').length,llmRequestsBefore);
    await llmDialog.getByRole('button',{name:'关闭服务接入',exact:true}).click();
    await unchanged(postCommit,postCommitRow,'Missing model API opens service settings without mock fallback or mutation');
    record('Old baseVersion is 409, invalidated candidate is 410; missing LLM configuration opens service settings without mock fallback');

    await page.locator('#candidate-agent-mode').selectOption('mock');
    batch=await action('POST','/edits/candidates',()=>page.getByTestId('generate-edit-candidates').click());
    await page.getByTestId('phase2-candidate-C').click();
    await page.getByTestId('cancel-edit-candidates').click();
    assert.equal(await page.getByTestId('phase2-candidate-preview').count(),0);
    await unchanged(postCommit,postCommitRow,'Cancelling candidates is read-only');
    record('Cancelling all previews leaves authoritative data and persistent locks byte-identical');

    // Real server-clock expiration with a short, explicit isolated-test TTL.
    const expiryBatch=await post('/edits/candidates',{baseVersion:postCommit.version,selectedComponentIds:[pm.componentId],instruction:'安排轻松一些',agentMode:'mock'});
    const expiryMs=Date.parse(expiryBatch.candidates[0].expiresAt)-Date.now()+900;
    assert.ok(expiryMs>0&&expiryMs<32000,'Bounded 30-second server TTL');
    await page.waitForTimeout(expiryMs);
    const expired=await post('/edits/commit',{baseVersion:postCommit.version,candidateId:expiryBatch.candidates[0].candidateId},410);
    assert.equal(expired.detail.code,'CANDIDATE_EXPIRED'); await unchanged(postCommit,postCommitRow,'Expired candidate leaves no partial update');
    record('Actual server-clock TTL expiry is rejected with 410 and unchanged formal state');

    batch=await action('POST','/edits/candidates',()=>page.getByTestId('generate-edit-candidates').click());
    await page.getByTestId('phase2-candidate-B').click();
    await page.evaluate(()=>{document.querySelector('.itinerary-column').scrollTop=0;document.querySelector('.control-panel').scrollTop=0;});
    const initial=await metrics(); const right=await page.locator('.control-panel').boundingBox();
    await page.mouse.move(right.x+right.width-20,right.y+right.height/2); await page.mouse.wheel(0,420); await page.waitForTimeout(180);
    const movedRight=await metrics(); assert.ok(movedRight.right>30); assert.equal(movedRight.left,initial.left);
    const left=await page.locator('.itinerary-column').boundingBox();
    await page.mouse.move(left.x+left.width-20,left.y+left.height/2); await page.mouse.wheel(0,420); await page.waitForTimeout(180);
    const movedLeft=await metrics(); assert.ok(movedLeft.left>30); assert.equal(movedLeft.right,movedRight.right); assert.equal(movedLeft.window,0);
    await page.setViewportSize({width:390,height:844}); await page.waitForTimeout(200);
    await page.getByTestId('phase2-candidates').evaluate(element=>window.scrollTo(0,element.getBoundingClientRect().top+window.scrollY-12));
    await page.waitForTimeout(100);
    await page.screenshot({path:path.join(shots,artifact('phase2-mobile-candidates.png'))});
    await page.getByTestId('phase2-candidate-preview').evaluate(element=>window.scrollTo(0,element.getBoundingClientRect().top+window.scrollY-12));
    await page.waitForTimeout(100);
    const mobile=await metrics(); assert.ok(mobile.width<=mobile.viewport+1,JSON.stringify(mobile));
    await page.screenshot({path:path.join(shots,artifact('phase2-mobile.png'))});
    assert.equal(await page.locator('iframe').count(),0); assert.equal(await page.evaluate(()=>window.__phase1LegacyExecuted),undefined);
    await unchanged(postCommit,postCommitRow,'Final preview/layout checks must remain read-only');
    record('Desktop panes remain independent; 390px readonly candidate preview has no overflow or runtime code execution');
    assert.deepEqual(errors,[]); assert.deepEqual(outbound,[],'No model/map/external network in mock preview');
    const report={passed:true,phase:editsStatus.phase,scope:'Phase 2 inherited regression',baseURL:base,realModel:false,realMap:false,generationMode:'mock',candidateTtlSeconds:editsStatus.candidateTtlSeconds,databaseIsolation:'.runtime/phase1-browser/travel.sqlite3',browserErrors:errors,externalRequests:outbound,results,screenshots:['phase2-preview-a.png','phase2-preview-b.png','phase2-preview-c.png','phase2-desktop.png','phase2-candidates.png','phase2-mobile.png','phase2-mobile-candidates.png'].map(artifact)};
    fs.writeFileSync(path.join(reports,artifact('phase2-browser.json')),JSON.stringify(report,null,2));
    console.log('RESULT '+JSON.stringify({passed:true,checks:results.length,browserErrors:errors,externalRequests:outbound}));
  }catch(error){
    await page.screenshot({path:path.join(shots,artifact('phase2-failure.png'))}).catch(()=>{});
    fs.writeFileSync(path.join(reports,artifact('phase2-browser.json')),JSON.stringify({passed:false,phase:'Phase 2',baseURL:base,realModel:false,realMap:false,results,browserErrors:errors,error:error.stack},null,2));
    console.error(error.stack); process.exitCode=1;
  }finally{await context.close();await browser.close();}
})();
