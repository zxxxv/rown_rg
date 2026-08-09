#!/usr/bin/env node
// 발표 D-1 자동 점검. 실패 시 비제로 종료 코드.

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { readdir, readFile } from "node:fs/promises";
import { extname, join } from "node:path";

const cwd = process.cwd();

const results = [];

function ok(name) {
  process.stdout.write(`  ✓ ${name}\n`);
  results.push({ name, ok: true });
}

function fail(name, err) {
  process.stdout.write(`  ✗ ${name}\n`);
  process.stdout.write(`    ${err.message}\n`);
  results.push({ name, ok: false, err });
}

async function check(name, fn) {
  try {
    await fn();
    ok(name);
  } catch (err) {
    fail(name, err);
  }
}

console.log("▸ 1/6 파일 존재 확인");
await check("public/mockServiceWorker.js", () => {
  if (!existsSync("public/mockServiceWorker.js")) {
    throw new Error("MSW worker 누락 — pnpm msw init public/ --save 실행 필요");
  }
});

await check("public/samples 더미 4종", () => {
  const files = ["sample.hwpx", "sample.pdf", "sample.md", "sample-refs.csv"];
  for (const f of files) {
    if (!existsSync(`public/samples/${f}`)) {
      throw new Error(`public/samples/${f} 누락`);
    }
  }
});

console.log("\n▸ 2/6 시연용 픽스처");
await check("proj_demo_w4 시연 프로젝트 존재", () => {
  const src = readFileSync("src/api/mock/fixtures/projects.ts", "utf-8");
  if (!src.includes('"proj_demo_w4"')) {
    throw new Error("W4_DEMO_PROJECT 픽스처 누락 — fixtures/projects.ts 확인");
  }
});

await check("자료 모순 픽스처 3건", () => {
  const src = readFileSync("src/api/mock/fixtures/contradictions.ts", "utf-8");
  const count = (src.match(/subject:/g) ?? []).length;
  if (count < 3) throw new Error(`모순 픽스처 ${count}건 (>=3 필요)`);
});

await check("소스 풀 (POOL) >= 20건", () => {
  const src = readFileSync("src/api/mock/fixtures/sources.ts", "utf-8");
  const count = (src.match(/source_kind:/g) ?? []).length;
  if (count < 20) throw new Error(`소스 풀 ${count}건 (>=20 필요)`);
});

console.log("\n▸ 3/6 라우트 등록");
await check("App.tsx에 필수 라우트 13개", () => {
  const app = readFileSync("src/App.tsx", "utf-8");
  const required = [
    '"/login"',
    '"/projects"',
    '"/projects/new"',
    '"/projects/:id/overview"',
    '"/projects/:id/sources"',
    '"/projects/:id/progress"',
    '"/projects/:id/preview"',
    '"/projects/:id/reconcile"',
    '"/projects/:id/editor"',
    '"/projects/:id/export"',
    '"/admin/dashboard"',
    '"/library"',
    '"/403"',
  ];
  const missing = required.filter((r) => !app.includes(r));
  if (missing.length > 0) {
    throw new Error(`누락 라우트: ${missing.join(", ")}`);
  }
});

await check("MSW 핸들러 등록 8종", () => {
  const handlers = readFileSync("src/api/mock/handlers.ts", "utf-8");
  const required = [
    "authHandlers",
    "projectsHandlers",
    "sourcesHandlers",
    "sectionsHandlers",
    "contradictionsHandlers",
    "progressHandlers",
    "adminHandlers",
    "libraryHandlers",
    "wsHandlers",
  ];
  const missing = required.filter((h) => !handlers.includes(`...${h}`));
  if (missing.length > 0) {
    throw new Error(`누락 핸들러: ${missing.join(", ")}`);
  }
});

console.log("\n▸ 4/6 디자인 토큰 일관성");
await check("src에 #hex 색상 0건", async () => {
  const dirs = ["src/components", "src/features", "src/pages"];
  const matches = [];
  const HEX = /#[0-9a-fA-F]{6}\b/g;
  async function walk(dir) {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const path = join(dir, e.name);
      if (e.isDirectory()) {
        await walk(path);
      } else if ([".ts", ".tsx"].includes(extname(path))) {
        const content = await readFile(path, "utf-8");
        const found = content.match(HEX);
        if (found) {
          for (const hex of found) matches.push({ path, hex });
        }
      }
    }
  }
  for (const d of dirs) await walk(d);
  if (matches.length > 0) {
    const sample = matches
      .slice(0, 5)
      .map((m) => `${m.path}: ${m.hex}`)
      .join("\n    ");
    throw new Error(`${matches.length}건 발견\n    ${sample}`);
  }
});

console.log("\n▸ 5/6 빌드·타입·린트");
function runScript(script) {
  return new Promise((resolve, reject) => {
    try {
      execSync(`pnpm ${script}`, { cwd, stdio: "pipe" });
      resolve();
    } catch (err) {
      const stdout = err.stdout?.toString() ?? "";
      const stderr = err.stderr?.toString() ?? "";
      reject(new Error(`pnpm ${script} 실패\n${stdout}\n${stderr}`.slice(0, 1500)));
    }
  });
}

await check("pnpm typecheck", () => runScript("typecheck"));
await check("pnpm lint", () => runScript("lint"));
await check("pnpm build", () => runScript("build"));

console.log("\n▸ 6/6 빌드 산출물 검증");
await check("dist/index.html에 /__components 경로 미노출", () => {
  const html = existsSync("dist/index.html") ? readFileSync("dist/index.html", "utf-8") : "";
  if (html.includes("__components")) {
    throw new Error("/__components가 dist/index.html에 노출됨");
  }
});

const passed = results.filter((r) => r.ok).length;
const failed = results.length - passed;
console.log(`\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
if (failed === 0) {
  console.log(`✅ ${passed}/${results.length} 항목 통과 — 발표 준비 완료`);
  process.exit(0);
} else {
  console.log(`❌ ${failed}/${results.length} 항목 실패`);
  process.exit(1);
}
