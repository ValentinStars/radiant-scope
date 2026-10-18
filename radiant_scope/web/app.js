'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let token = '', state = null, paused = false, chartWindow = 60, interval = 1000, currentView = 'overview', selectedPacket = null;
let localSettings = {};
try { localSettings = JSON.parse(localStorage.getItem('radiant-settings') || '{}'); } catch (_) { /* Storage may be disabled. */ }
const settings = {bpf:'', monitor:false, motion:true, refresh:1000, ...localSettings};
const palette = ['#c1f76b','#7eb6f6','#d5b6f9','#e5bc79','#82d5c1'];
function setText(id, value) { $(id).textContent = value ?? '—'; }
function formatBytes(value, rate = false) {
  const n = Number(value) || 0;
  const units = rate ? ['B/s','KB/s','MB/s','GB/s'] : ['B','KB','MB','GB','TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(Math.max(1,n)) / Math.log(1000)));
  return [new Intl.NumberFormat('en-US', {maximumFractionDigits:i ? 2 : 0}).format(n / 1000 ** i), units[i]];
}
const bytes = value => formatBytes(value).join(' ');
const duration = value => { const n = Math.max(0, Math.floor(value || 0)); return [Math.floor(n / 3600), Math.floor(n / 60) % 60, n % 60].map(x => String(x).padStart(2,'0')).join(':'); };
function toast(message) { setText('toast', message); $('toast').classList.remove('hidden'); clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').classList.add('hidden'), 6000); }
async function post(path, body = {}) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-Radiant-Token':token}, body:JSON.stringify(body)});
  const data = await r.json();
  if (!r.ok) throw Error(data.error || 'Ошибка запроса');
  return data;
}
function saveSettings() {
  Object.assign(settings, {bpf:$('bpf').value, monitor:$('monitor').checked, motion:$('motion').checked, refresh:Number($('refresh').value)});
  interval = settings.refresh;
  document.body.classList.toggle('no-motion', !settings.motion);
  try { localStorage.setItem('radiant-settings', JSON.stringify(settings)); } catch (_) {}
}
function navigate() {
  const titles = {overview:['Обзор сети','Каждый сигнал.<br><span>Полная картина.</span>','Живая телеметрия и анализ сети в одном пространстве.'], packets:['Инспектор пакетов','Внутри<br><span>каждого пакета.</span>','Адреса, протоколы и события вашей сети в реальном времени.'], wireless:['Wi-Fi окружение','Эфир<br><span>вокруг вас.</span>','Точки доступа и распределение по каналам.'], settings:['Настройки','Ваш инструмент.<br><span>Ваши правила.</span>','Параметры захвата, отображения и готовность системы.']};
  currentView = location.hash.slice(1);
  if (!titles[currentView]) currentView = 'overview';
  document.querySelectorAll('.view').forEach(x => x.classList.toggle('hidden', x.id !== 'view-' + currentView));
  document.querySelectorAll('[data-view]').forEach(x => x.classList.toggle('active', x.dataset.view === currentView));
  setText('breadcrumb', titles[currentView][0]);
  $('page-title').innerHTML = titles[currentView][1];
  setText('page-description', titles[currentView][2]);
  if (state) render();
}
window.addEventListener('hashchange', navigate);
function canvasContext(id) {
  const c = $(id), rect = c.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const scale = Math.min(devicePixelRatio || 1, 2);
  c.width = Math.round(rect.width * scale); c.height = Math.round(rect.height * scale);
  const ctx = c.getContext('2d'); ctx.scale(scale, scale);
  return {ctx, w:rect.width, h:rect.height};
}
function chart(id, series, options = {}) {
  const area = canvasContext(id); if (!area) return;
  const {ctx, w, h} = area, grid = !!options.grid, pad = grid ? 23 : 2;
  const flattened = series.flatMap(s => s.values.filter(x => x !== null && Number.isFinite(x)));
  const min = options.min ?? 0, max = options.max ?? Math.max(1000, ...flattened) * 1.2;
  const y = v => h - pad - ((v - min) / Math.max(1, max - min)) * (h - pad * 2);
  if (grid) {
    ctx.font = '9px monospace'; ctx.textBaseline = 'bottom';
    for (let i = 0; i < 4; i++) {
      const value = min + (max-min) * i / 3, py = y(value);
      ctx.strokeStyle = '#2b302c'; ctx.setLineDash([3,5]); ctx.beginPath();ctx.moveTo(0,py);ctx.lineTo(w,py);ctx.stroke();
      ctx.fillStyle = '#a0aa9f'; ctx.fillText(options.signal ? Math.round(value) : formatBytes(value, true).join(' '), 0, py - 4);
    }
    ctx.setLineDash([]);
  }
  if (!flattened.length) {
    ctx.fillStyle = '#a0aa9f';ctx.font = '11px sans-serif';ctx.textAlign = 'center';
    ctx.fillText(options.signal ? 'RSSI недоступен для этого интерфейса' : 'Ожидание измерений…', w/2, h/2);return;
  }
  for (const s of series) {
    const n = s.values.length, span = options.span || Math.max(n,2);
    const x = i => w - (n-1-i) * w / Math.max(1, span - 1);
    let segment = [];
    function drawSegment() {
      if (!segment.length) return;
      ctx.beginPath(); segment.forEach((p,i) => i ? ctx.lineTo(p[0],p[1]) : ctx.moveTo(p[0],p[1]));
      ctx.strokeStyle = s.color;ctx.lineWidth = grid ? 1.8 : 1.4;ctx.lineJoin='round';ctx.stroke();
      const last = segment[segment.length-1];
      ctx.lineTo(last[0], h-pad);ctx.lineTo(segment[0][0], h-pad);ctx.closePath();
      const grad = ctx.createLinearGradient(0,pad,0,h);grad.addColorStop(0,s.color+'30');grad.addColorStop(1,s.color+'01');ctx.fillStyle=grad;ctx.fill();
      ctx.beginPath();ctx.arc(last[0]-2,last[1],2.5,0,Math.PI*2);ctx.fillStyle=s.color;ctx.fill();segment=[];
    }
    s.values.forEach((v,i) => {if(v === null || !Number.isFinite(v)) drawSegment(); else segment.push([x(i),y(v)]);});
    drawSegment();
  }
}
function renderCharts() {
  if (!state || currentView !== 'overview') return;
  const history = state.history.slice(-chartWindow);
  const series = (key,color) => ({values:history.map(s => s[key]),color});
  chart('traffic-chart', [series('rx','#b9ed7c'),series('tx','#7eb6f6')], {grid:true,span:chartWindow});
  chart('signal-chart', [series('rssi','#c1f76b')], {min:-100,max:-30,grid:true,signal:true,span:chartWindow});
  for (const [id,key,color] of [['rx-spark','rx','#b9ed7c'],['tx-spark','tx','#7eb6f6'],['pps-spark','pps','#d5b6f9']]) {
    chart(id,[{values:state.history.slice(-40).map(x=>x[key]),color}],{span:40,max:Math.max(10,...state.history.slice(-40).map(x=>x[key]))*1.3});
  }
  setText('range-start', `−${chartWindow} сек`);
}
function renderPackets() {
  const query = $('packet-search').value.toLowerCase(), protocol = $('protocol-filter').value;
  const list = state.packets.filter(p => (!protocol || p.protocol === protocol) && (!query || [p.source,p.destination,p.protocol,p.info].join(' ').toLowerCase().includes(query))).reverse();
  $('packets-empty').classList.toggle('hidden', !!list.length);
  if (!list.length && (query || protocol)) $('packets-empty').innerHTML = '<div class="empty-icon">⌕</div><h3>Пакетов не найдено</h3><p>Измените поисковый запрос или фильтр протокола.</p>';
  else if (!list.length) $('packets-empty').innerHTML = '<div class="empty-icon">≋</div><h3>Сначала — захват</h3><p>Выберите интерфейс и нажмите «Начать захват».<br>Пакеты появятся здесь в реальном времени.</p>';
  $('packet-rows').innerHTML = list.map(p => `<tr tabindex="0" data-packet="${p.id}" aria-label="Пакет ${p.id}, ${esc(p.protocol)}, ${p.length} байт"><td>${p.id}</td><td>${esc(new Date(p.time*1000).toLocaleTimeString('ru-RU',{hour12:false}))}</td><td>${esc(p.source)}</td><td>${esc(p.destination)}</td><td><span class="protocol-pill">${esc(p.protocol)}</span></td><td>${p.length} B</td><td>${p.rssi ?? '—'}</td><td title="${esc(p.info)}">${esc(p.info)}</td></tr>`).join('');
}
function renderWireless() {
  setText('scan-status', state.scan_error || (state.scan_time ? `Обновлено ${new Date(state.scan_time*1000).toLocaleTimeString('ru-RU')} · ${state.networks.length} точек доступа` : 'Выберите Wi-Fi-интерфейс и обновите список.'));
  $('networks').innerHTML = state.networks.map(n => `<div class="network"><div><strong>${esc(n.ssid)}</strong> ${n.in_use ? '<span class="tag">CONNECTED</span>' : ''}<small>${esc(n.bssid)} · CH ${n.channel} · ${esc(n.frequency)} · ${esc(n.security || 'OPEN')}</small></div><div class="strength">${n.signal}%<small>СИГНАЛ</small></div></div>`).join('');
  const channels = {};for(const n of state.networks) channels[n.channel]=(channels[n.channel]||0)+1;
  const max = Math.max(1,...Object.values(channels));
  $('channels').innerHTML = Object.keys(channels).length ? Object.entries(channels).sort((a,b)=>Number(a[0])-Number(b[0])).map(([ch,n])=>`<div class="channel-row"><span>CH ${ch}</span><div class="bar"><i></i></div><span>${n}</span></div>`).join('') : '<div class="empty">Нет данных окружения.</div>';
  $('channels').querySelectorAll('.bar i').forEach((el,i)=>el.style.width = Object.entries(channels).sort((a,b)=>Number(a[0])-Number(b[0]))[i][1]/max*100+'%');
}
function render() {
  const s = state.history.at(-1) || {}, wifi = state.wifi, cap = state.capture;
  $('demo-banner').classList.toggle('hidden', !state.demo);
  if ($('interface').value !== state.interface) $('interface').value = state.interface;
  setText('session-time',duration(state.uptime));
  setText('capture-indicator',cap.running ? '● ЗАХВАТ АКТИВЕН' : '● ТЕЛЕМЕТРИЯ АКТИВНА');
  $('capture-indicator').classList.toggle('capturing',cap.running);
  setText('capture',cap.running ? '■ Остановить захват' : '▶ Начать захват');
  $('capture').classList.toggle('danger',cap.running);
  $('capture-error').classList.toggle('hidden',!cap.error);setText('capture-error',cap.error);
  const rssi = s.rssi;
  const quality = rssi == null ? null : Math.max(0,Math.min(100,2*(rssi+100)));
  setText('rssi',rssi == null ? '—' : Number(rssi).toFixed(0));
  setText('rssi-quality',quality == null ? 'Нет RSSI' : quality > 75 ? 'Отличный' : quality > 50 ? 'Хороший' : quality > 25 ? 'Слабый' : 'Очень слабый');
  setText('ssid',wifi.ssid || (wifi.mode === 'monitor' ? 'Monitor interface' : 'Нет ассоциации'));
  setText('quality',quality == null ? '—' : `${Math.round(quality)}%`);
  $('gauge-fill').style.strokeDasharray = `${quality || 0} 100`;
  document.querySelectorAll('.signal-scale i').forEach((el,i)=>el.classList.toggle('lit',quality != null && i < quality / 5));
  for (const key of ['rx','tx']) { const [n,unit]=formatBytes(s[key],true);setText(key,n);setText(key+'-unit',unit);setText(key+'-total',bytes(s[key+'_bytes'])+' · всего'); }
  setText('pps',Math.round(s.pps || 0).toLocaleString('en-US'));
  setText('drops',((s.rx_dropped||0)+(s.tx_dropped||0))+' drops');
  setText('errors',((s.rx_errors||0)+(s.tx_errors||0))+' ошибок интерфейса');
  setText('channel',wifi.channel);setText('frequency',wifi.frequency ? (wifi.frequency/1000).toFixed(3)+' GHz' : '—');setText('width',wifi.width);setText('bitrate',wifi.bitrate ? wifi.bitrate+' Mbps' : '—');setText('wifi-mode',wifi.mode || 'NO RADIO');
  const total = Object.values(state.protocols).reduce((a,b)=>a+b,0);
  const protocols = Object.entries(state.protocols).sort((a,b)=>b[1]-a[1]).slice(0,5);
  $('protocols').innerHTML = protocols.length ? protocols.map(([p,n])=>`<div class="protocol-row"><div><span>${esc(p)}</span><span>${(n/total*100).toFixed(1)}% · ${n}</span></div><div class="bar"><i></i></div></div>`).join('') : '<div class="empty compact">Начните захват для анализа протоколов.</div>';
  $('protocols').querySelectorAll('.bar i').forEach((el,i)=>{el.style.width=protocols[i][1]/total*100+'%';el.style.background=palette[i];});
  $('talkers').innerHTML = state.talkers.length ? state.talkers.slice(0,4).map(([ip,n],i)=>`<div class="talker"><span class="rank">0${i+1}</span><span class="address" title="${esc(ip)}">${esc(ip)}</span><span class="volume">${bytes(n)}</span></div>`).join('') : '<div class="empty compact">Здесь появятся источники пакетов.</div>';
  setText('nav-count',cap.count > 999 ? (cap.count/1000).toFixed(1)+'k' : cap.count);
  setText('packet-total',cap.count.toLocaleString('en-US'));
  const pf = $('protocol-filter'), previous = pf.value;
  pf.innerHTML = '<option value="">Все протоколы</option>'+Object.keys(state.protocols).sort().map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');pf.value=previous;
  if (currentView === 'packets') renderPackets();
  if (currentView === 'wireless') renderWireless();
  renderCharts();
  setText('footer-stats',`1s resolution · ${state.history.length} samples · ${cap.buffered} buffered packets`);
}
async function poll() {
  try {
    if (!paused) {
      const r = await fetch('/api/state');if(!r.ok) throw Error('Server offline');
      state = await r.json();render();$('offline').classList.add('hidden');setText('connection','Локальное соединение');
    }
  } catch (_) { $('offline').classList.remove('hidden');setText('connection','Нет соединения'); }
  setText('clock',new Date().toLocaleTimeString('ru-RU',{hour12:false}));
  setTimeout(poll,interval);
}
$('interface').addEventListener('change',async event=>{
  const select=event.target;select.disabled=true;
  try {await post('/api/interface',{name:select.value});toast('Интерфейс изменён. История и буфер очищены.');selectedPacket=null;$('packet-detail').classList.add('hidden');const r=await fetch('/api/state');state=await r.json();render();}
  catch(e){toast(e.message);if(state)select.value=state.interface;}finally{select.disabled=false;}
});
$('capture').addEventListener('click',async()=>{
  const button=$('capture');button.disabled=true;
  try {
    const stop=state?.capture.running;
    await post(stop?'/api/capture/stop':'/api/capture/start',{filter:$('bpf').value,monitor:$('monitor').checked});
    selectedPacket=null;$('packet-detail').classList.add('hidden');
    const r=await fetch('/api/state');state=await r.json();render();
    toast(stop?'Захват остановлен. Буфер доступен для экспорта.':'Захват запускается. Статус и ошибки появятся на панели.');
  }catch(e){toast(e.message);}finally{button.disabled=false;}
});
$('pause').addEventListener('click',()=>{paused=!paused;setText('pause',paused?'▶ Продолжить UI':'Ⅱ Пауза UI');setText('connection',paused?'Отображение на паузе':'Локальное соединение');});
$('scan').addEventListener('click',async()=>{const b=$('scan');b.disabled=true;setText('scan-status','Чтение окружения…');try{await post('/api/scan');const r=await fetch('/api/state');state=await r.json();renderWireless();}catch(e){toast(e.message);}finally{b.disabled=false;}});
$('packet-search').addEventListener('input',()=>state&&renderPackets());$('protocol-filter').addEventListener('change',()=>state&&renderPackets());
function openPacket(id){const p=state?.packets.find(x=>x.id===Number(id));if(!p)return;selectedPacket=p;const fields={'Номер':p.id,'Время':new Date(p.time*1000).toISOString(),'Источник':p.source,'Назначение':p.destination,'Протокол':p.protocol,'Размер':p.length+' B','RSSI':p.rssi==null?'Недоступен':p.rssi+' dBm','Информация':p.info};$('detail-fields').innerHTML=Object.entries(fields).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');$('packet-detail').classList.remove('hidden');$('packet-detail').scrollIntoView({behavior:settings.motion?'smooth':'instant',block:'nearest'});}
$('packet-rows').addEventListener('click',e=>{const row=e.target.closest('tr');if(row)openPacket(row.dataset.packet);});
$('packet-rows').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){const row=e.target.closest('tr');if(row){e.preventDefault();openPacket(row.dataset.packet);}}});
$('close-detail').addEventListener('click',()=>$('packet-detail').classList.add('hidden'));
document.querySelectorAll('[data-window]').forEach(b=>b.addEventListener('click',()=>{chartWindow=Number(b.dataset.window);document.querySelectorAll('[data-window]').forEach(x=>x.classList.toggle('selected',x===b));renderCharts();}));
document.querySelectorAll('[data-bpf]').forEach(b=>b.addEventListener('click',()=>{$('bpf').value=b.dataset.bpf;saveSettings();}));
for(const id of ['bpf','monitor','motion','refresh']) $(id).addEventListener('change',saveSettings);
window.addEventListener('resize',()=>{clearTimeout(window.resizeTimer);window.resizeTimer=setTimeout(renderCharts,100);});
document.addEventListener('keydown',e=>{if(e.key==='1'&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName))location.hash='overview';});
async function init(){
  $('bpf').value=settings.bpf;$('monitor').checked=settings.monitor;$('motion').checked=settings.motion;$('refresh').value=String(settings.refresh);saveSettings();navigate();
  try{
    const r=await fetch('/api/init');if(!r.ok)throw Error('Init failed');const data=await r.json();token=data.token;
    $('interface').innerHTML=data.interfaces.map(x=>`<option value="${esc(x.name)}">${esc(x.name)} · ${esc(x.kind.toUpperCase())} · ${esc(x.state)}</option>`).join('');
    $('tools').innerHTML=Object.entries(data.tools).map(([name,exists])=>`<div class="tool"><span>${esc(name)}</span><span class="${exists?'available':'missing'}">${exists?'✓ Установлен':'× Не найден'}</span></div>`).join('');
    const r2=await fetch('/api/state');state=await r2.json();$('interface').value=state.interface;
    const describe=()=>{const x=data.interfaces.find(x=>x.name===$('interface').value);setText('interface-note',x ? `${x.addresses[0]||'Нет IP'} · ${x.mac}` : '');};describe();$('interface').addEventListener('change',describe);
    render();poll();
  }catch(_){$('offline').classList.remove('hidden');setTimeout(init,2500);}
}
init();
