(() => {
  const get = (id) => document.getElementById(id);
  const device = 'petsense-esp32-001';
  let token = '', frame = null, online = false, busy = false, pending = null, clicked = 0;
  const mib = (n) => Number.isFinite(n) && n >= 0 ? `${(n / 1048576).toFixed(1)} MiB` : '未上报';
  const message = (s) => { get('captureMessage').textContent = s; };
  async function api(path, body) {
    const response = await fetch(path, {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Petsense-Session': token},
      body: JSON.stringify(body), signal: AbortSignal.timeout(10000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) { token = ''; get('captureLogin').hidden = false; }
      throw Error(data.error || data.message || `请求失败 (${response.status})，请确认账号绑定及设备状态`);
    }
    return data;
  }
  function render() {
    const f = frame || {};
    const supported = typeof f.sd_free_bytes === 'number' && typeof f.storage_draining === 'boolean';
    const ready = online && supported && f.sd_ready && f.utc_us && f.clock_age_us < 300000000 &&
      !f.disk_errors && !f.queue_drops && !f.storage_draining && f.sd_free_bytes > 1048576;
    get('storage').textContent = online ? `SD 剩余 ${mib(f.sd_free_bytes)} / 总计 ${mib(f.sd_total_bytes)}` : 'SD 剩余容量：设备离线';
    get('capture').textContent = f.capture_active ? '停止采集' : '开始采集数据';
    get('capture').disabled = busy || !!pending || !online || !(f.capture_active || ready);
    get('captureState').textContent = !online ? '设备离线' : !supported ? '需要更新支持采集状态的板端固件' :
      `${f.capture_active ? '正在采集' : f.storage_draining ? '正在写入剩余数据，请勿断电' : '未采集'} · 已落盘 ${f.persisted_rows ?? 0} 条 · 写入错误 ${f.disk_errors ?? 0}`;
  }
  window.addEventListener('edge-status', ({detail}) => {
    online = !!detail.online; frame = detail.payload || null;
    if (pending && online && frame?.boot_id === pending.boot && frame.seq > pending.seq &&
        (pending.action === 'start' ? frame.capture_active && frame.session_id === pending.session : !frame.capture_active && !frame.storage_draining)) {
      message(pending.action === 'start' ? '板端已确认开始采集' : '板端已确认停止并写完队列'); pending = null;
    }
    render();
  });
  setInterval(() => {
    if (pending && Date.now() - pending.at > 20000) {
      pending = null; message('未收到板端确认，请核对实时状态。请求可能已经执行，不要拔卡。'); render();
    }
  }, 1000);
  get('captureLogin').addEventListener('submit', async (event) => {
    event.preventDefault(); if (busy) return; busy = true; render();
    const form = event.currentTarget;
    try {
      const data = await api('/api/app/auth/login', {username: form.elements.username.value, password: form.elements.password.value});
      token = data.token; form.hidden = true; message('已登录，请点击采集按钮');
    } catch (e) { message(e.message); }
    finally { form.elements.password.value = ''; busy = false; render(); }
  });
  get('capture').addEventListener('click', async () => {
    if (busy || pending || get('capture').disabled || Date.now() - clicked < 1000) return;
    clicked = Date.now();
    if (!token) { get('captureLogin').hidden = false; message('请先登录绑定此设备的账号'); return; }
    busy = true; render();
    const action = frame.capture_active ? 'stop' : 'start';
    const session = action === 'stop' ? frame.session_id : `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const boot = frame.boot_id;
    const seq = frame.seq;
    try {
      if (action === 'start') await api('/api/collection/v1/sessions', {id: session, device});
      await api(`/api/collection/v1/sessions/${encodeURIComponent(session)}/control`, {action});
      pending = {action, session, boot, seq, at: Date.now()}; message('命令已发送，等待板端确认');
    } catch (e) { message(e.message); }
    finally { busy = false; render(); }
  });
})();
