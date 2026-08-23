(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.HDFollowSettingsForm = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function createFollowFormRenderGuard() {
    return {
      dirty: false,
      rendering: false,
    };
  }

  function beginFollowFormRender(guard, options) {
    const state = guard || createFollowFormRenderGuard();
    const opts = options || {};
    if (state.dirty && !opts.force) return false;
    state.rendering = true;
    return true;
  }

  function finishFollowFormRender(guard) {
    if (guard) guard.rendering = false;
  }

  function markFollowFormDirty(guard) {
    if (!guard || guard.rendering) return false;
    guard.dirty = true;
    return true;
  }

  function markFollowFormClean(guard) {
    if (!guard) return false;
    guard.dirty = false;
    return true;
  }

  function getMarketRuleToggleLabel(enabled) {
    return enabled ? '启用' : '禁跟';
  }

  function getMarketRuleToggleTitle(marketName, enabled) {
    const name = String(marketName || '该玩法');
    return enabled ? `${name}当前启用，点击后设为禁跟` : `${name}当前禁跟，点击后恢复启用`;
  }

  function buildFollowFormStatusModel(options) {
    const opts = options || {};
    const saving = !!opts.saving;
    const dirty = !!opts.dirty;
    const updatedAt = String(opts.updatedAt || '').trim() || '—';
    if (saving) {
      return {
        updatedText: '正在保存...',
        saveLabel: '保存中...',
        saveDisabled: true,
      };
    }
    if (dirty) {
      return {
        updatedText: '未保存更改',
        saveLabel: '保存策略*',
        saveDisabled: false,
      };
    }
    return {
      updatedText: updatedAt,
      saveLabel: '保存策略',
      saveDisabled: false,
    };
  }

  return {
    buildFollowFormStatusModel,
    beginFollowFormRender,
    createFollowFormRenderGuard,
    finishFollowFormRender,
    getMarketRuleToggleLabel,
    getMarketRuleToggleTitle,
    markFollowFormClean,
    markFollowFormDirty,
  };
});
