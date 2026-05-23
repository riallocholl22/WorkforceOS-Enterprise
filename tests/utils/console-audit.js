const SEVERE_CONSOLE_TYPES = new Set(["error"]);

const severeWarningPatterns = [
  /content security policy/i,
  /\bcsp\b/i,
  /react.*error/i,
  /react.*hydration/i,
  /unhandled/i,
  /websocket.*(failed|error|closed before|refused)/i,
  /failed to load resource/i,
  /chunkloaderror/i,
  /networkerror/i
];

const allowedNoise = [
  /favicon\.ico/i,
  /service worker.*(only.*secure origins|failed.*register)/i,
  /failed to load resource: net::ERR_CONNECTION_RESET/i
];

function isAllowed(message) {
  return allowedNoise.some((pattern) => pattern.test(message));
}

function attachConsoleAudit(page, testInfo) {
  const findings = [];
  const requests = new Map();

  function record(kind, message, detail = {}) {
    const text = String(message || "");
    if (isAllowed(text)) return;
    findings.push({
      kind,
      message: text,
      detail
    });
  }

  page.on("console", (message) => {
    const type = message.type();
    const text = message.text();
    if (SEVERE_CONSOLE_TYPES.has(type)) {
      record(`console.${type}`, text);
      return;
    }
    if (type === "warning" && severeWarningPatterns.some((pattern) => pattern.test(text))) {
      record("console.warning", text);
    }
  });

  page.on("pageerror", (error) => {
    record("pageerror", error.message, { stack: error.stack });
  });

  page.on("request", (request) => {
    requests.set(request, Date.now());
  });

  page.on("requestfailed", (request) => {
    const failure = request.failure();
    const url = request.url();
    if (/net::ERR_ABORTED/i.test(failure?.errorText || "")) {
      return;
    }
    if (/net::ERR_CONNECTION_RESET/i.test(failure?.errorText || "") && /^https?:\/\/127\.0\.0\.1:5500\/(css|js)\//i.test(url)) {
      return;
    }
    if (/\/enterprise\/(ops|security)\/stream/i.test(url)) {
      record("websocket.failure", `${url} ${failure?.errorText || "failed"}`);
      return;
    }
    record("request.failed", `${request.method()} ${url} ${failure?.errorText || "failed"}`);
  });

  page.on("response", (response) => {
    const status = response.status();
    const url = response.url();
    if (status >= 500) {
      record("response.5xx", `${status} ${url}`);
    }
    if (status === 403 && /content-security-policy|csp/i.test(url)) {
      record("csp.violation", `${status} ${url}`);
    }
  });

  return {
    findings,
    assertHealthy() {
      if (findings.length === 0) return;
      const summary = findings
        .slice(0, 20)
        .map((item, index) => `${index + 1}. ${item.kind}: ${item.message}`)
        .join("\n");
      throw new Error(`Browser audit detected severe runtime issues in ${testInfo.title}:\n${summary}`);
    }
  };
}

module.exports = {
  attachConsoleAudit
};
