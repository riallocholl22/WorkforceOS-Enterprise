import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const frontendDir = path.join(root, "frontend");
const failures = [];

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full);
      continue;
    }
    if (!/\.(html|css|js)$/i.test(entry.name)) continue;
    const text = fs.readFileSync(full, "utf8");
    if (/\bdebugger\b/.test(text)) failures.push(`${path.relative(root, full)} contains debugger`);
    if (/console\.log\(/.test(text)) failures.push(`${path.relative(root, full)} contains console.log`);
    if (/<script[^>]+src=["']https?:\/\//i.test(text)) {
      failures.push(`${path.relative(root, full)} loads remote scripts directly`);
    }
  }
}

walk(frontendDir);

if (failures.length) {
  console.error("Frontend lint failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("Frontend lint passed.");
