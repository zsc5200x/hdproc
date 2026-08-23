(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.HDAddNodeForm = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function createDefaultAddNodeForm() {
    return {
      mode: 'account',
      account: {
        base_url: '',
        username: '',
        password: '',
        login_tab: 3,
        relogin_min_interval_seconds: 5,
        security_code: '',
        monitor_members: [],
        monitor_downlines: [],
      },
      signal: {
        enabled: true,
        url: '',
        token: '',
        live_only: true,
        limit: 200,
        interval_seconds: 1,
      },
    };
  }

  function toTrimmedString(value) {
    return String(value || '').trim();
  }

  function buildAccountPayload(account) {
    const current = account || {};
    const payload = {
      base_url: toTrimmedString(current.base_url),
      username: toTrimmedString(current.username),
      password: String(current.password || ''),
      tab: Number(current.login_tab || 3),
      relogin_min_interval_seconds: Number(current.relogin_min_interval_seconds || 5),
      security_code: toTrimmedString(current.security_code),
    };
    const monitorDownlines = (Array.isArray(current.monitor_downlines) ? current.monitor_downlines : [])
      .map(toTrimmedString)
      .filter(Boolean);
    if (payload.tab === 2) {
      payload.login2_watch_members = (Array.isArray(current.monitor_members) ? current.monitor_members : [])
        .map(toTrimmedString)
        .filter(Boolean);
      payload.login2_watch_downlines = monitorDownlines;
    } else if (payload.tab === 3 && monitorDownlines.length) {
      payload.login2_watch_downlines = monitorDownlines;
    }
    if (!payload.base_url || !payload.username || !payload.password) {
      throw new Error('请填写：平台网址 / 登录账号 / 登录密码');
    }
    return payload;
  }

  function buildSignalPayload(signal) {
    const current = signal || {};
    const payload = {
      signal_pull_enabled: !!current.enabled,
      signal_pull_url: toTrimmedString(current.url),
      signal_pull_token: toTrimmedString(current.token),
      signal_pull_live_only: !!current.live_only,
      signal_pull_limit: Number(current.limit || 200),
      signal_pull_interval_seconds: Number(current.interval_seconds || 1),
    };
    if (payload.signal_pull_enabled && !payload.signal_pull_url) {
      throw new Error('请填写：远端信号地址');
    }
    return payload;
  }

  function buildAddNodeSubmitRequest(form) {
    const current = form || {};
    const mode = current.mode === 'signal' ? 'signal' : 'account';
    if (mode === 'signal') {
      return {
        endpoint: '/api/signal-pull-config',
        payload: buildSignalPayload(current.signal),
      };
    }
    return {
      endpoint: '/api/accounts/add',
      payload: buildAccountPayload(current.account),
    };
  }

  return {
    createDefaultAddNodeForm,
    buildAddNodeSubmitRequest,
  };
});
