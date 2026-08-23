const assert = require("node:assert/strict");
const form = require("../app/webui/follow_settings_form.js");

const clean = form.buildFollowFormStatusModel({
  updatedAt: "2026-05-24 03:05:21",
  dirty: false,
  saving: false,
});

assert.equal(clean.updatedText, "2026-05-24 03:05:21");
assert.equal(clean.saveLabel, "保存策略");
assert.equal(clean.saveDisabled, false);

const dirty = form.buildFollowFormStatusModel({
  updatedAt: "2026-05-24 03:05:21",
  dirty: true,
  saving: false,
});

assert.equal(dirty.updatedText, "未保存更改");
assert.equal(dirty.saveLabel, "保存策略*");
assert.equal(dirty.saveDisabled, false);

const saving = form.buildFollowFormStatusModel({
  updatedAt: "2026-05-24 03:05:21",
  dirty: true,
  saving: true,
});

assert.equal(saving.updatedText, "正在保存...");
assert.equal(saving.saveLabel, "保存中...");
assert.equal(saving.saveDisabled, true);

console.log("follow_settings_form tests passed");
