import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const ROOT = process.cwd();
const SRC_DIR = path.join(ROOT, "src");
const APP_PATH = path.join(SRC_DIR, "App.tsx");

const DYNAMIC_TOKEN = "__SEGMENT__";

function normalizeRoutePattern(routePath) {
  if (!routePath) {
    return "";
  }
  const canonical = routePath.startsWith("/") ? routePath : `/${routePath}`;
  const trimmed = canonical.replace(/\/+$/, "");
  if (trimmed === "") {
    return "/";
  }
  const normalized = trimmed
    .split("/")
    .map((segment) => (segment.startsWith(":") ? DYNAMIC_TOKEN : segment))
    .join("/");
  return normalized || "/";
}

function normalizeLinkTarget(targetPath) {
  if (!targetPath.startsWith("/")) {
    return "";
  }
  const noHash = targetPath.split("#", 1)[0];
  const noQuery = noHash.split("?", 1)[0];
  const trimmed = noQuery.replace(/\/+$/, "");
  const normalized = (trimmed || "/")
    .split("/")
    .map((segment) => (segment.includes("${") ? DYNAMIC_TOKEN : segment))
    .join("/");
  return normalized || "/";
}

function routeMatches(routePattern, linkPath) {
  const routeSegments = routePattern.split("/").filter(Boolean);
  const linkSegments = linkPath.split("/").filter(Boolean);

  for (let i = 0; i < routeSegments.length; i += 1) {
    const routeSegment = routeSegments[i];
    if (routeSegment === "*") {
      return true;
    }
    if (i >= linkSegments.length) {
      return false;
    }
    if (routeSegment === DYNAMIC_TOKEN) {
      continue;
    }
    if (routeSegment !== linkSegments[i]) {
      return false;
    }
  }
  return routeSegments.length === linkSegments.length;
}

async function listFilesRecursively(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        return listFilesRecursively(fullPath);
      }
      if (entry.isFile() && /\.tsx?$/.test(entry.name)) {
        return [fullPath];
      }
      return [];
    }),
  );
  return files.flat();
}

function collectRoutePatterns(appSource) {
  const routePaths = new Set();
  const routeRegex = /<Route\s+[^>]*path=(["'`])([^"'`]+)\1/g;
  let match;
  while ((match = routeRegex.exec(appSource)) !== null) {
    const routePath = match[2];
    const normalized = normalizeRoutePattern(routePath);
    if (normalized) {
      routePaths.add(normalized);
    }
    if (routePath.startsWith("items/") || routePath.startsWith("admin/")) {
      const scoped = normalizeRoutePattern(`/libraries/:libraryId/${routePath}`);
      if (scoped) {
        routePaths.add(scoped);
      }
    }
  }
  return routePaths;
}

function collectLinkTargets(fileSource) {
  const targets = [];

  const toRegex = /\bto=(["'`])([^"'`]+)\1/g;
  let match;
  while ((match = toRegex.exec(fileSource)) !== null) {
    targets.push(match[2]);
  }

  const navigateRegex = /\bnavigate\((["'`])([^"'`]+)\1/g;
  while ((match = navigateRegex.exec(fileSource)) !== null) {
    targets.push(match[2]);
  }

  return targets
    .map((target) => normalizeLinkTarget(target))
    .filter((target) => target.startsWith("/"));
}

async function main() {
  const appSource = await readFile(APP_PATH, "utf8");
  const routePatterns = collectRoutePatterns(appSource);
  if (!routePatterns.size) {
    throw new Error("No route paths detected in App.tsx");
  }

  const files = await listFilesRecursively(SRC_DIR);
  const allTargets = new Set();
  for (const filePath of files) {
    if (filePath === APP_PATH) {
      continue;
    }
    const source = await readFile(filePath, "utf8");
    for (const target of collectLinkTargets(source)) {
      allTargets.add(target);
    }
  }

  const unmatchedTargets = [...allTargets].filter((target) => {
    const normalizedTarget = normalizeRoutePattern(target);
    return ![...routePatterns].some((pattern) =>
      routeMatches(pattern, normalizedTarget),
    );
  });

  if (unmatchedTargets.length) {
    console.error("Route-link audit failed. Unmatched link targets:");
    for (const target of unmatchedTargets.sort()) {
      console.error(` - ${target}`);
    }
    process.exit(1);
  }

  console.log(
    `Route-link audit passed: ${allTargets.size} link targets match ${routePatterns.size} route patterns.`,
  );
}

await main();
