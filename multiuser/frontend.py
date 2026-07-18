"""사용자용 단일 페이지(가입·로그인·키등록·내 자산·자동매매 베타).

프레임워크 없이 순수 HTML/JS. /auth/* API를 호출한다(쿠키 세션).
web_auth 의 GET /app 라우트가 이 문자열을 그대로 서빙한다.
"""

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>stockagent · 내 자산</title>
<style>
  :root { --bg:#eef1f5; --card:#fff; --line:#e5e7eb; --ink:#1f2430; --sub:#6b7280;
          --brand:#2563eb; --brand2:#1d4ed8; --up:#e5484d; --down:#1e7f4f; --soft:#f3f4f6; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:13px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:760px; margin:0 auto; padding:18px 14px 70px; }
  .top { display:flex; align-items:center; justify-content:space-between; margin-bottom:4px; }
  .brand { display:flex; align-items:baseline; gap:8px; }
  .brand b { font-size:19px; letter-spacing:-.02em; }
  .brand span { font-size:11px; color:var(--sub); }
  h2 { font-size:14px; margin:0; }
  .muted { color:var(--sub); font-size:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:16px; margin-top:14px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
  .row { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  label { display:block; font-size:12px; color:var(--sub); margin:10px 0 4px; }
  input { width:100%; padding:10px 12px; font-size:13px; border:1px solid var(--line);
          border-radius:9px; background:#fff; }
  input:focus { outline:none; border-color:var(--brand); box-shadow:0 0 0 3px rgba(37,99,235,.12); }
  button { font-size:13px; padding:10px 14px; border-radius:9px; border:1px solid var(--brand);
           background:var(--brand); color:#fff; cursor:pointer; font-weight:600; }
  button:hover { background:var(--brand2); }
  button.ghost { background:#fff; color:var(--ink); border-color:var(--line); font-weight:500; }
  button.ghost:hover { background:var(--soft); }
  button.sm { padding:6px 10px; font-size:12px; }
  button:disabled { opacity:.5; cursor:default; }
  .tabs { display:flex; gap:6px; margin-bottom:6px; }
  .tabs button { flex:1; background:#fff; color:var(--sub); border-color:var(--line); font-weight:500; }
  .tabs button.on { background:var(--brand); color:#fff; border-color:var(--brand); }
  table { width:100%; border-collapse:collapse; margin-top:10px; }
  th,td { text-align:right; padding:9px 6px; border-bottom:1px solid var(--line); font-variant-numeric:tabular-nums; }
  th:first-child, td:first-child { text-align:left; }
  th { color:var(--sub); font-weight:600; font-size:11px; }
  .up { color:var(--up); } .down { color:var(--down); }
  .big { font-size:24px; font-weight:800; letter-spacing:-.02em; }
  .err { color:var(--up); font-size:12px; margin-top:8px; min-height:16px; }
  .hidden { display:none; }
  .note { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:9px;
          padding:10px 12px; font-size:12px; margin-top:10px; }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:700; }
  .b-buy { background:#fdecec; color:var(--up); } .b-sell { background:#e7f4ec; color:var(--down); }
  .b-hold { background:var(--soft); color:var(--sub); }
  .beta { font-size:10px; background:var(--soft); color:var(--sub); padding:1px 6px; border-radius:6px; margin-left:6px; }
  .dlist { margin-top:10px; }
  .drow { border-top:1px solid var(--line); padding:9px 0; }
  .drow:first-child { border-top:0; }
  .drow .rsn { color:var(--sub); font-size:12px; margin-top:2px; }
  .foot { text-align:center; color:var(--sub); font-size:11px; margin-top:24px; line-height:1.7; }
  .switch { display:flex; align-items:center; gap:8px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand"><b>stockagent</b><span>내 자산 · 자동매매</span></div>
    <div id="userbar" class="hidden row" style="gap:8px;">
      <span id="userEmail" class="muted"></span>
      <a id="adminLink" class="hidden" href="/admin" style="font-size:12px;color:var(--brand);text-decoration:none;">관리자</a>
      <button class="ghost sm" onclick="logout()">로그아웃</button>
    </div>
  </div>

  <!-- 로그인 / 가입 -->
  <div id="authView" class="card hidden" style="max-width:420px;margin:32px auto 0;">
    <div class="tabs">
      <button id="tabLogin" class="on" onclick="setMode('login')">로그인</button>
      <button id="tabSignup" onclick="setMode('signup')">회원가입</button>
    </div>
    <div id="nameField" class="hidden">
      <label>이름(선택)</label>
      <input id="fName" autocomplete="name">
    </div>
    <div id="termsField" class="hidden" style="margin-top:10px;">
      <label style="display:flex;align-items:flex-start;gap:6px;cursor:pointer;">
        <input type="checkbox" id="agreeTerms" style="width:auto;margin:2px 0 0;">
        <span style="font-size:12px;color:var(--ink);line-height:1.4;">
          (필수) 자동매매 이용에 따른 투자 손실의 책임은 전적으로 본인에게 있으며, 플랫폼은 수익을 보장하거나 손실을 보상하지 않음에 동의합니다.
        </span>
      </label>
    </div>
    <label>이메일</label>
    <input id="fEmail" type="email" autocomplete="email">
    <label>비밀번호 <span class="muted">(8자 이상)</span></label>
    <input id="fPassword" type="password" autocomplete="current-password"
           onkeydown="if(event.key==='Enter')submitAuth()">
    <div class="err" id="authErr"></div>
    <button id="authBtn" style="width:100%;margin-top:6px;" onclick="submitAuth()">로그인</button>
    <div style="margin-top:12px;text-align:center;font-size:12px;">
      <a href="#" onclick="alert('현재 비밀번호 찾기 기능은 준비 중입니다. 관리자(admin@example.com)에게 문의해 주세요.');return false;" class="muted" style="text-decoration:underline;">비밀번호를 잊으셨나요?</a>
    </div>
  </div>

  <!-- 로그인 후 -->
  <div id="dashView" class="hidden">
    <!-- 자산 요약 -->
    <div class="card">
      <div class="row"><h2>내 자산</h2><button class="ghost sm" onclick="loadPortfolio()">새로고침</button></div>
      <div id="pfSummary" class="muted" style="margin-top:8px;">불러오는 중…</div>
      <div id="pfTableWrap"></div>
      <div class="err" id="pfErr"></div>
    </div>

    <!-- 자동매매 (베타) -->
    <div class="card">
      <div class="row">
        <h2>자동매매<span class="beta">BETA</span></h2>
        <button id="runBtn" class="sm" onclick="runOnce()">지금 한 번 실행(모의)</button>
      </div>
      <div class="muted" style="margin-top:6px;">AI가 지금 시세로 매수/매도/관망을 어떻게 판단하는지 <b>모의로</b> 확인합니다. 실제 주문은 나가지 않아요.</div>
      <div class="row" style="margin-top:12px;gap:12px;flex-wrap:wrap;">
        <div style="flex:1;min-width:180px;">
          <label>대상 종목 <span class="muted">(쉼표로 구분, 예: BTC, ETH)</span></label>
          <input id="sTickers" placeholder="BTC, ETH">
        </div>
        <div style="width:140px;">
          <label>1회 최대 주문(원)</label>
          <input id="sMax" type="number" placeholder="10000">
        </div>
        <button class="ghost sm" style="align-self:flex-end;" onclick="saveSettings()">설정 저장</button>
      </div>
      <div class="err" id="tradeErr"></div>
      <div id="runResult" class="dlist"></div>
    </div>

    <!-- 거래소 키 -->
    <div class="card">
      <h2>업비트 연결</h2>
      <div id="keyStatus" class="muted" style="margin-top:6px;">확인 중…</div>
      <div id="keyForm" class="hidden">
        <div class="note">
          🔒 <b>출금 권한이 없는</b> 키만 등록할 수 있어요(자산조회·주문만).
          업비트 API 키 생성 시 <b>‘출금’은 체크하지 마세요.</b> IP 주소 등록도 권장합니다.
        </div>
        <label>Access Key</label>
        <input id="kAccess" autocomplete="off">
        <label>Secret Key</label>
        <input id="kSecret" type="password" autocomplete="off">
        <div class="err" id="keyErr"></div>
        <button id="keyBtn" style="margin-top:6px;" onclick="saveKey()">키 등록</button>
      </div>
    </div>

    <!-- 내 계정 -->
    <div class="card">
      <h2>내 계정</h2>
      <div class="muted" id="acctInfo" style="margin-top:6px;"></div>
      
      <div style="margin:16px 0;padding:12px;background:var(--bg);border-radius:6px;border:1px solid var(--border);">
        <div style="font-size:14px;font-weight:600;margin-bottom:4px;">현재 요금제: <span id="acctTier" style="color:var(--brand);">Free</span></div>
        <div style="font-size:12px;color:var(--ink);margin-bottom:10px;">Free 요금제는 모의투자 및 수동 1회 실행만 가능합니다. 100% 자동매매를 원하시면 Pro 요금제로 업그레이드하세요.</div>
        <button class="ghost sm" style="background:var(--brand);color:white;border:none;" onclick="alert('결제 시스템(Portone/Stripe) 연동 준비 중입니다.')">Pro 업그레이드 (준비 중)</button>
      </div>

      <label>이름</label>
      <div class="row" style="gap:8px;">
        <input id="acName" placeholder="이름">
        <button class="ghost sm" onclick="saveProfile()">저장</button>
      </div>
      <label style="margin-top:14px;">비밀번호 변경</label>
      <input id="acCur" type="password" placeholder="현재 비밀번호" autocomplete="current-password">
      <input id="acNew" type="password" placeholder="새 비밀번호(8자 이상)" autocomplete="new-password" style="margin-top:6px;">
      <div class="err" id="acErr"></div>
      <div class="row" style="margin-top:6px;">
        <button class="ghost sm" onclick="changePassword()">비밀번호 변경</button>
        <button class="ghost sm" style="color:var(--up);border-color:#f3c0c2;" onclick="deleteAccount()">회원 탈퇴</button>
      </div>
    </div>

    <!-- 과거 내역 (History) -->
    <div class="card">
      <div class="row">
        <h2>최근 기록</h2>
        <button class="ghost sm" onclick="loadHistory()">새로고침</button>
      </div>
      <div class="tabs" style="margin-top:12px;margin-bottom:12px;">
        <button id="tabHistTrade" class="on" onclick="setHistTab('trade')" style="padding:4px 8px;font-size:13px;">체결 내역</button>
        <button id="tabHistDecision" onclick="setHistTab('decision')" style="padding:4px 8px;font-size:13px;">AI 판단 로그</button>
      </div>
      <div id="histTradeView">
        <div id="histTrades" class="muted">기록이 없습니다.</div>
      </div>
      <div id="histDecisionView" class="hidden">
        <div id="histDecisions" class="muted">기록이 없습니다.</div>
      </div>
    </div>

    <div class="foot">
      투자 판단과 그 결과(손실 포함)에 대한 책임은 전적으로 본인에게 있습니다.<br>
      본 서비스는 사용자 본인 계정을 자동화하는 도구이며, 자동매매 기능은 베타입니다.
    </div>
  </div>
</div>

<script>
let mode = 'login';
const $ = (id) => document.getElementById(id);
async function api(path, opts={}) {
  const r = await fetch(path, { credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, ...opts });
  let body = null; try { body = await r.json(); } catch(e){}
  return { ok:r.ok, status:r.status, body };
}
function won(n){ return (Math.round(n||0)).toLocaleString('ko-KR')+'원'; }
function pct(n){ const c=(n||0)>=0?'up':'down'; return '<span class="'+c+'">'+((n||0)>=0?'+':'')+(n||0).toFixed(2)+'%</span>'; }

function setMode(m){
  mode = m;
  $('tabLogin').classList.toggle('on', m==='login');
  $('tabSignup').classList.toggle('on', m==='signup');
  $('nameField').classList.toggle('hidden', m!=='signup');
  $('termsField').classList.toggle('hidden', m!=='signup');
  $('authBtn').textContent = m==='login' ? '로그인' : '회원가입';
  $('authErr').textContent='';
}
async function submitAuth(){
  $('authErr').textContent='';
  const payload = { email:$('fEmail').value.trim(), password:$('fPassword').value };
  if(mode==='signup') {
    if(!$('agreeTerms').checked) {
      $('authErr').textContent='이용약관 및 면책 조항에 동의해야 합니다.';
      return;
    }
    payload.display_name = $('fName').value.trim();
  }
  $('authBtn').disabled=true;
  const r = await api(mode==='login'?'/auth/login':'/auth/register',
    { method:'POST', body:JSON.stringify(payload) });
  $('authBtn').disabled=false;
  if(!r.ok){ $('authErr').textContent=(r.body&&r.body.error)||'실패했습니다.'; return; }
  boot();
}
async function logout(){ await api('/auth/logout',{method:'POST'}); boot(); }

async function boot(){
  const me = await api('/auth/me');
  const user = me.body && me.body.user;
  if(!user){
    $('authView').classList.remove('hidden'); $('dashView').classList.add('hidden');
    $('userbar').classList.add('hidden'); setMode('login'); return;
  }
  $('userEmail').textContent = user.email;
  $('userbar').classList.remove('hidden');
  $('adminLink').classList.toggle('hidden', !user.is_admin);
  const tier = user.subscription_tier || 'Free';
  $('acctTier').textContent = tier.toUpperCase();
  $('acctInfo').innerHTML = user.email + ' · 가입 ' + (user.created_at||'').slice(0,10) + (user.is_admin?' · <b>관리자</b>':'');
  $('acName').value = user.display_name || '';
  $('authView').classList.add('hidden'); $('dashView').classList.remove('hidden');
  await refreshKeyStatus(); loadPortfolio(); loadSettings(); loadHistory();
}

async function saveProfile(){
  const r = await api('/auth/account/profile',{method:'POST',body:JSON.stringify({display_name:$('acName').value.trim()})});
  if(r.ok) $('acctInfo').style.opacity=.5, setTimeout(()=>$('acctInfo').style.opacity=1,300);
}
async function changePassword(){
  $('acErr').textContent='';
  const r = await api('/auth/account/password',{method:'POST',
    body:JSON.stringify({current_password:$('acCur').value,new_password:$('acNew').value})});
  if(!r.ok){ $('acErr').textContent=(r.body&&r.body.error)||'변경 실패'; return; }
  alert('비밀번호가 변경되었습니다. 다시 로그인해 주세요.'); boot();
}
async function deleteAccount(){
  if(!confirm('정말 탈퇴하시겠어요? 계정·등록한 키·기록이 모두 삭제됩니다.')) return;
  const pw = prompt('확인을 위해 비밀번호를 입력하세요.'); if(pw===null) return;
  const r = await api('/auth/account/delete',{method:'POST',body:JSON.stringify({password:pw})});
  if(!r.ok){ alert((r.body&&r.body.error)||'탈퇴 실패'); return; }
  alert('탈퇴되었습니다.'); boot();
}

async function refreshKeyStatus(){
  const r = await api('/auth/credentials');
  const creds = (r.body && r.body.credentials) || [];
  if(creds.length){
    $('keyStatus').innerHTML = '✅ 업비트 연결됨 <span class="muted">('+creds[0].access_key_masked+')</span> ' +
      '<button class="ghost sm" onclick="removeKey()">연결 해제</button>';
    $('keyForm').classList.add('hidden');
  } else {
    $('keyStatus').textContent = '아직 업비트 키를 등록하지 않았어요.';
    $('keyForm').classList.remove('hidden');
  }
}
async function removeKey(){
  await api('/auth/credentials',{method:'DELETE',body:JSON.stringify({label:'default'})});
  await refreshKeyStatus(); loadPortfolio();
}
async function saveKey(){
  $('keyErr').textContent=''; $('keyBtn').disabled=true; $('keyBtn').textContent='검증 중…';
  const r = await api('/auth/credentials',{ method:'POST',
    body:JSON.stringify({ access_key:$('kAccess').value.trim(), secret_key:$('kSecret').value.trim() }) });
  $('keyBtn').disabled=false; $('keyBtn').textContent='키 등록';
  if(!r.ok){ $('keyErr').textContent=(r.body&&r.body.error)||'등록 실패'; return; }
  $('kAccess').value=''; $('kSecret').value='';
  await refreshKeyStatus(); loadPortfolio();
}

async function loadPortfolio(){
  $('pfErr').textContent=''; $('pfSummary').textContent='불러오는 중…'; $('pfTableWrap').innerHTML='';
  const r = await api('/auth/portfolio');
  if(!r.ok){
    $('pfSummary').textContent='';
    $('pfErr').textContent = (r.body&&r.body.need_key) ? '업비트 키를 먼저 등록하면 자산이 보여요.'
                                                       : ((r.body&&r.body.error)||'자산 조회 실패');
    return;
  }
  const pf = r.body.portfolio;
  $('pfSummary').innerHTML = '<div class="big">'+won(pf.total_value)+'</div>' +
    '<div class="muted">평가손익 '+pct(pf.total_return_pct)+' · 투자원금 '+won(pf.total_principal)+'</div>';
  const items = pf.items||[]; if(!items.length){ return; }
  let h='<table><thead><tr><th>종목</th><th>평가금액</th><th>수익률</th><th>비중</th></tr></thead><tbody>';
  for(const it of items){
    const name = it.ticker==='KRW' ? '원화(KRW)' : it.currency;
    h+='<tr><td>'+name+'</td><td>'+won(it.current_value)+'</td><td>'+
       (it.ticker==='KRW'?'—':pct(it.return_pct))+'</td><td>'+(it.weight||0).toFixed(1)+'%</td></tr>';
  }
  $('pfTableWrap').innerHTML = h+'</tbody></table>';
}

async function loadSettings(){
  const r = await api('/auth/settings'); const s = r.body && r.body.settings; if(!s) return;
  $('sTickers').value = (s.tickers||[]).map(t=>t.replace('KRW-','')).join(', ');
  $('sMax').value = s.max_order_krw;
}
async function saveSettings(){
  $('tradeErr').textContent='';
  const r = await api('/auth/settings',{ method:'POST', body:JSON.stringify({
    tickers:$('sTickers').value, max_order_krw:parseInt($('sMax').value||'10000',10) }) });
  if(!r.ok){ $('tradeErr').textContent=(r.body&&r.body.error)||'저장 실패'; return; }
  loadSettings();
}

function badge(side){
  const s=(side||'none').toLowerCase();
  if(s==='buy') return '<span class="badge b-buy">매수</span>';
  if(s==='sell') return '<span class="badge b-sell">매도</span>';
  return '<span class="badge b-hold">관망</span>';
}
async function runOnce(){
  $('tradeErr').textContent=''; $('runBtn').disabled=true; $('runBtn').textContent='실행 중…';
  const r = await api('/auth/trade/run_once',{method:'POST'});
  $('runBtn').disabled=false; $('runBtn').textContent='지금 한 번 실행(모의)';
  if(!r.ok){
    $('runResult').innerHTML='';
    $('tradeErr').textContent=(r.body&&r.body.need_key)?'업비트 키를 먼저 등록하세요.':((r.body&&r.body.error)||'실행 실패');
    return;
  }
  const res = (r.body && r.body.results) || [];
  let h='<div class="muted" style="margin:10px 0 2px;">모의 실행 결과 · 실제 주문 아님</div>';
  for(const d of res){
    if(d.error){ h+='<div class="drow">'+d.ticker+' <span class="up">오류: '+d.error+'</span></div>'; continue; }
    h+='<div class="drow"><div class="row"><div><b>'+d.ticker.replace('KRW-','')+'</b> '+badge(d.order)+
       ' <span class="muted">신뢰도 '+(d.confidence||0).toFixed(2)+'</span></div>'+
       '<div class="muted">'+won(d.price)+'</div></div>'+
       '<div class="rsn">'+(d.reasoning||d.order_reason||'')+'</div></div>';
  }
  $('runResult').innerHTML = h;
}

let histMode = 'trade';
function setHistTab(m){
  histMode = m;
  $('tabHistTrade').classList.toggle('on', m==='trade');
  $('tabHistDecision').classList.toggle('on', m==='decision');
  $('histTradeView').classList.toggle('hidden', m!=='trade');
  $('histDecisionView').classList.toggle('hidden', m!=='decision');
}
async function loadHistory(){
  $('histTrades').textContent='불러오는 중…';
  $('histDecisions').textContent='불러오는 중…';
  const r = await api('/auth/trade/history');
  if(!r.ok){
    $('histTrades').textContent='조회 실패';
    $('histDecisions').textContent='조회 실패';
    return;
  }
  const trades = r.body.trades || [];
  const decs = r.body.decisions || [];

  if(!trades.length) $('histTrades').textContent='거래 내역이 없습니다.';
  else {
    let h='<table style="font-size:12px;"><thead><tr><th>시간</th><th>종목</th><th>구분</th><th>가격</th><th>수량</th><th>금액</th></tr></thead><tbody>';
    for(const t of trades){
      h+='<tr><td class="muted">'+(t.ts||'').slice(5,16).replace('T',' ')+'</td><td>'+t.ticker.replace('KRW-','')+'</td>'+
         '<td>'+badge(t.side)+'</td><td>'+won(t.price)+'</td><td>'+(t.volume||0).toFixed(4)+'</td>'+
         '<td>'+won(t.krw_amount)+' '+(t.dry_run?'<span class="muted">(모의)</span>':'')+'</td></tr>';
    }
    $('histTrades').innerHTML = h+'</tbody></table>';
  }

  if(!decs.length) $('histDecisions').textContent='AI 판단 기록이 없습니다.';
  else {
    let h='';
    for(const d of decs){
      h+='<div class="drow"><div class="row" style="font-size:12px;"><div><span class="muted">'+(d.ts||'').slice(5,16).replace('T',' ')+'</span> '+
         '<b>'+d.ticker.replace('KRW-','')+'</b> '+badge(d.order_side)+'</div>'+
         '<div>'+won(d.price)+' <span class="muted">(신뢰도 '+(d.confidence||0).toFixed(2)+')</span></div></div>'+
         '<div class="rsn" style="margin-top:4px;">'+(d.reasoning||'')+'</div></div>';
    }
    $('histDecisions').innerHTML = h;
  }
}

boot();
</script>
</body>
</html>
"""


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>stockagent · 회원 관리</title>
<style>
  :root { --bg:#eef1f5; --card:#fff; --line:#e5e7eb; --ink:#1f2430; --sub:#6b7280;
          --brand:#2563eb; --up:#e5484d; --down:#1e7f4f; --soft:#f3f4f6; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:13px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:960px; margin:0 auto; padding:18px 14px 60px; }
  .top { display:flex; align-items:center; justify-content:space-between; }
  b.brand { font-size:19px; }
  a { color:var(--brand); text-decoration:none; font-size:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; margin-top:14px;
          box-shadow:0 1px 2px rgba(16,24,40,.04); overflow-x:auto; }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:9px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { color:var(--sub); font-weight:600; font-size:11px; }
  .muted { color:var(--sub); }
  .pill { display:inline-block; padding:1px 7px; border-radius:999px; font-size:11px; font-weight:700; }
  .on { background:#e7f4ec; color:var(--down); } .off { background:#fdecec; color:var(--up); }
  .yes { color:var(--down); font-weight:700; } .no { color:var(--sub); }
  button { font-size:12px; padding:5px 9px; border-radius:7px; border:1px solid var(--line);
           background:#fff; color:var(--ink); cursor:pointer; margin-right:4px; }
  button.danger { color:var(--up); border-color:#f3c0c2; }
  .err { color:var(--up); margin-top:10px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <b class="brand">회원 관리 <span class="muted" style="font-size:12px;font-weight:400;">admin</span></b>
    <a href="/app">← 내 대시보드</a>
  </div>
  <div class="card">
    <div id="summary" class="muted">불러오는 중…</div>
    <div id="err" class="err"></div>
    <table id="tbl" class="hidden">
      <thead><tr>
        <th>ID</th><th>이메일</th><th>이름</th><th>가입일</th><th>최근로그인</th>
        <th>키</th><th>자동매매</th><th>상태</th><th>관리</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
</div>
<script>
const $ = (id)=>document.getElementById(id);
async function api(p,o={}){ const r=await fetch(p,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...o});
  let b=null; try{b=await r.json();}catch(e){} return {ok:r.ok,status:r.status,body:b}; }
function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
async function load(){
  const r = await api('/auth/admin/users');
  if(r.status===401){ location.href='/app'; return; }
  if(r.status===403){ $('summary').textContent=''; $('err').textContent='관리자 권한이 필요합니다.'; return; }
  if(!r.ok){ $('err').textContent=(r.body&&r.body.error)||'불러오기 실패'; return; }
  const us = r.body.users||[];
  $('summary').textContent = '총 ' + us.length + '명 · 키 등록 ' + us.filter(u=>u.has_key).length + '명 · 자동매매 ' + us.filter(u=>u.auto_enabled).length + '명';
  $('tbl').classList.remove('hidden');
  $('rows').innerHTML = us.map(u=>{
    const act = u.is_active
      ? '<button onclick="act('+u.id+',\'deactivate\')">비활성화</button>'
      : '<button onclick="act('+u.id+',\'activate\')">활성화</button>';
    return '<tr>'+
      '<td>'+u.id+'</td>'+
      '<td>'+esc(u.email)+(u.is_admin?' <span class="pill on">admin</span>':'')+'</td>'+
      '<td>'+esc(u.display_name||'—')+'</td>'+
      '<td class="muted">'+(u.created_at||'').slice(0,10)+'</td>'+
      '<td class="muted">'+((u.last_login_at||'').slice(0,10)||'—')+'</td>'+
      '<td>'+(u.has_key?'<span class="yes">있음</span>':'<span class="no">없음</span>')+'</td>'+
      '<td>'+(u.auto_enabled?'<span class="yes">ON</span>':'<span class="no">off</span>')+'</td>'+
      '<td>'+(u.is_active?'<span class="pill on">활성</span>':'<span class="pill off">비활성</span>')+'</td>'+
      '<td>'+act+'<button class="danger" onclick="del('+u.id+',\''+esc(u.email)+'\')">삭제</button></td>'+
      '</tr>';
  }).join('');
}
async function act(id,action){
  const r = await api('/auth/admin/users/'+id,{method:'POST',body:JSON.stringify({action})});
  if(!r.ok){ alert((r.body&&r.body.error)||'실패'); return; } load();
}
async function del(id,email){
  if(!confirm('회원 '+email+' 을(를) 삭제할까요? 계정·키·기록이 모두 삭제됩니다.')) return;
  const r = await api('/auth/admin/users/'+id,{method:'POST',body:JSON.stringify({action:'delete'})});
  if(!r.ok){ alert((r.body&&r.body.error)||'실패'); return; } load();
}
load();
</script>
</body>
</html>
"""
