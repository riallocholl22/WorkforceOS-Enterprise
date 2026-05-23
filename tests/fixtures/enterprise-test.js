const base = require("@playwright/test");
const { attachConsoleAudit } = require("../utils/console-audit");
const { installEnterpriseApiStabilizers } = require("../utils/network");

const test = base.test.extend({
  audit: async ({ page }, use, testInfo) => {
    const audit = attachConsoleAudit(page, testInfo);
    await use(audit);
    audit.assertHealthy();
  },
  enterprisePage: async ({ page, audit }, use) => {
    await installEnterpriseApiStabilizers(page);
    try {
      await use(page);
    } finally {
      await page.goto("about:blank").catch(() => {});
      await page.close({ runBeforeUnload: false }).catch(() => {});
    }
  }
});

module.exports = {
  test,
  expect: base.expect
};
