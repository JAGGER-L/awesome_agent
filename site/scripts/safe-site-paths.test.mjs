import assert from "node:assert/strict";
import {
  link,
  lstat,
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  atomicWriteSafeSiteFile,
  assertSafeSiteDirectory,
  assertSafeSiteFile,
  replaceSafeSiteDirectory,
  SafeSitePathError,
} from "./safe-site-paths.mjs";

function metadata(kind) {
  return {
    isDirectory: () => kind === "directory" || kind === "reparse",
    isFile: () => kind === "file",
    isReparsePoint: () => kind === "reparse",
    isSymbolicLink: () => kind === "link",
    dev: 1,
    ino: kind,
  };
}

function missingError(path) {
  return Object.assign(new Error(`Missing fixture path: ${path}`), { code: "ENOENT" });
}

function fakeFileSystem(records, { realPaths = new Map() } = {}) {
  const nodes = new Map(records.map(([path, kind]) => [resolve(path), kind]));
  const identities = new Map(
    [...realPaths].map(([path, identity]) => [resolve(path), resolve(identity)]),
  );
  return {
    async lstat(path) {
      const key = resolve(path);
      const kind = nodes.get(key);
      if (kind === undefined) throw missingError(key);
      if (kind instanceof Error) throw kind;
      return metadata(kind);
    },
    async realpath(path) {
      const key = resolve(path);
      if (!nodes.has(key)) throw missingError(key);
      return identities.get(key) ?? key;
    },
  };
}

const site = resolve("virtual", "site");
const source = join(site, "src");
const content = join(source, "content");
const generatedDocs = join(content, "docs");
const dist = join(site, "dist");
const llms = join(dist, "llms.txt");

test("accepts only an ordinary contained directory chain", async () => {
  const existing = fakeFileSystem([
    [site, "directory"],
    [source, "directory"],
    [content, "directory"],
    [generatedDocs, "directory"],
  ]);
  const result = await assertSafeSiteDirectory({
    siteDirectory: site,
    targetDirectory: generatedDocs,
    fileSystem: existing,
  });
  assert.equal(result.exists, true);
  assert.equal(result.projectedTarget, generatedDocs);

  const missingTarget = fakeFileSystem([
    [site, "directory"],
    [source, "directory"],
    [content, "directory"],
  ]);
  const missingResult = await assertSafeSiteDirectory({
    siteDirectory: site,
    targetDirectory: generatedDocs,
    allowMissingTarget: true,
    fileSystem: missingTarget,
  });
  assert.equal(missingResult.exists, false);
  assert.equal(missingResult.projectedTarget, generatedDocs);

  const missingSuffix = fakeFileSystem([
    [site, "directory"],
    [source, "directory"],
  ]);
  const projectedResult = await assertSafeSiteDirectory({
    siteDirectory: site,
    targetDirectory: generatedDocs,
    allowMissingTarget: true,
    fileSystem: missingSuffix,
  });
  assert.equal(projectedResult.exists, false);
  assert.equal(projectedResult.projectedTarget, generatedDocs);
});

test("rejects the site root itself and lexical paths outside it", async () => {
  const fileSystem = fakeFileSystem([[site, "directory"]]);
  await assert.rejects(
    assertSafeSiteDirectory({
      siteDirectory: site,
      targetDirectory: site,
      fileSystem,
    }),
    SafeSitePathError,
  );
  await assert.rejects(
    assertSafeSiteDirectory({
      siteDirectory: site,
      targetDirectory: resolve(site, "..", "outside"),
      allowMissingTarget: true,
      fileSystem,
    }),
    SafeSitePathError,
  );
});

test("rejects a symlink, junction, or reparse point at every existing component", async () => {
  const chain = [site, source, content, generatedDocs];
  for (const unsafePath of chain) {
    const records = chain.map((path) => [
      path,
      path === unsafePath ? "link" : "directory",
    ]);
    await assert.rejects(
      assertSafeSiteDirectory({
        siteDirectory: site,
        targetDirectory: generatedDocs,
        fileSystem: fakeFileSystem(records),
      }),
      /symlink, junction, or reparse point/u,
      unsafePath,
    );
  }

  const reparseRecords = chain.map((path) => [
    path,
    path === content ? "reparse" : "directory",
  ]);
  await assert.rejects(
    assertSafeSiteDirectory({
      siteDirectory: site,
      targetDirectory: generatedDocs,
      fileSystem: fakeFileSystem(reparseRecords),
    }),
    /symlink, junction, or reparse point/u,
  );
});

test("rejects a non-directory ancestor and an existing target of the wrong kind", async () => {
  await assert.rejects(
    assertSafeSiteDirectory({
      siteDirectory: site,
      targetDirectory: generatedDocs,
      fileSystem: fakeFileSystem([
        [site, "directory"],
        [source, "file"],
      ]),
    }),
    /requires an ordinary directory/u,
  );
  await assert.rejects(
    assertSafeSiteDirectory({
      siteDirectory: site,
      targetDirectory: generatedDocs,
      fileSystem: fakeFileSystem([
        [site, "directory"],
        [source, "directory"],
        [content, "directory"],
        [generatedDocs, "file"],
      ]),
    }),
    /requires an ordinary directory/u,
  );
});

test("rejects an ordinary-looking component whose real identity escapes the site root", async () => {
  const outside = resolve("virtual", "outside", "content");
  const fileSystem = fakeFileSystem(
    [
      [site, "directory"],
      [source, "directory"],
      [content, "directory"],
      [generatedDocs, "directory"],
    ],
    { realPaths: new Map([[content, outside]]) },
  );
  await assert.rejects(
    assertSafeSiteDirectory({
      siteDirectory: site,
      targetDirectory: generatedDocs,
      fileSystem,
    }),
    /escapes the real site root/u,
  );
});

test("fails closed when a required target is absent or inspection is denied", async () => {
  const missingTarget = fakeFileSystem([
    [site, "directory"],
    [source, "directory"],
    [content, "directory"],
  ]);
  await assert.rejects(
    assertSafeSiteDirectory({
      siteDirectory: site,
      targetDirectory: generatedDocs,
      fileSystem: missingTarget,
    }),
    /does not exist/u,
  );

  const denied = Object.assign(new Error("denied"), { code: "EACCES" });
  await assert.rejects(
    assertSafeSiteDirectory({
      siteDirectory: site,
      targetDirectory: generatedDocs,
      allowMissingTarget: true,
      fileSystem: fakeFileSystem([
        [site, "directory"],
        [source, denied],
      ]),
    }),
    /cannot inspect/u,
  );
});

test("allows a missing or ordinary llms file but rejects linked and non-file targets", async () => {
  const directories = [
    [site, "directory"],
    [dist, "directory"],
  ];
  const missing = await assertSafeSiteFile({
    siteDirectory: site,
    targetFile: llms,
    allowMissingTarget: true,
    fileSystem: fakeFileSystem(directories),
  });
  assert.equal(missing.exists, false);

  const ordinary = await assertSafeSiteFile({
    siteDirectory: site,
    targetFile: llms,
    fileSystem: fakeFileSystem([...directories, [llms, "file"]]),
  });
  assert.equal(ordinary.exists, true);

  for (const kind of ["link", "reparse", "directory"]) {
    await assert.rejects(
      assertSafeSiteFile({
        siteDirectory: site,
        targetFile: llms,
        fileSystem: fakeFileSystem([...directories, [llms, kind]]),
      }),
      SafeSitePathError,
      kind,
    );
  }

  await assert.rejects(
    assertSafeSiteFile({
      siteDirectory: site,
      targetFile: llms,
      allowMissingTarget: true,
      fileSystem: fakeFileSystem([
        [site, "directory"],
        [dist, "link"],
      ]),
    }),
    /symlink, junction, or reparse point/u,
  );

  const outsideLlms = resolve("virtual", "outside", "llms.txt");
  await assert.rejects(
    assertSafeSiteFile({
      siteDirectory: site,
      targetFile: llms,
      fileSystem: fakeFileSystem([...directories, [llms, "file"]], {
        realPaths: new Map([[llms, outsideLlms]]),
      }),
    }),
    /escapes the real site root/u,
  );
});

async function temporarySite(t) {
  const root = await mkdtemp(join(tmpdir(), "awesome-safe-site-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const siteDirectory = join(root, "site");
  await mkdir(siteDirectory);
  return { root, siteDirectory };
}

test("generated docs have an ordinary versioned parent scaffold", async () => {
  const marker = fileURLToPath(new URL("../src/content/.gitkeep", import.meta.url));
  const parentMetadata = await lstat(dirname(marker));
  const markerMetadata = await lstat(marker);

  assert.equal(parentMetadata.isDirectory(), true);
  assert.equal(parentMetadata.isSymbolicLink(), false);
  assert.equal(parentMetadata.isReparsePoint?.() ?? false, false);
  assert.equal(markerMetadata.isFile(), true);
  assert.equal(markerMetadata.isSymbolicLink(), false);
  assert.equal(markerMetadata.isReparsePoint?.() ?? false, false);
});

test("atomic file replacement never follows a target swapped to an external hard link", async (t) => {
  const { root, siteDirectory } = await temporarySite(t);
  const outputDirectory = join(siteDirectory, "dist");
  const targetFile = join(outputDirectory, "llms.txt");
  const sentinel = join(root, "outside-sentinel.txt");
  await mkdir(outputDirectory);
  await writeFile(targetFile, "old", "utf8");
  await writeFile(sentinel, "outside", "utf8");

  let injected = false;
  const racingFileSystem = {
    async rename(source, target) {
      if (!injected && target === targetFile) {
        injected = true;
        await rm(targetFile);
        await link(sentinel, targetFile);
      }
      return rename(source, target);
    },
  };

  await atomicWriteSafeSiteFile({
    siteDirectory,
    targetFile,
    content: "replacement",
    label: "llms race regression",
    fileSystem: racingFileSystem,
  });

  assert.equal(await readFile(sentinel, "utf8"), "outside");
  assert.equal(await readFile(targetFile, "utf8"), "replacement");
  assert.deepEqual(
    (await readdir(outputDirectory)).filter((name) => name !== "llms.txt"),
    [],
  );
});

test("atomic file replacement fails closed when its parent becomes an external junction", async (t) => {
  const { root, siteDirectory } = await temporarySite(t);
  const outputDirectory = join(siteDirectory, "dist");
  const preservedOutput = join(siteDirectory, "preserved-dist");
  const outsideDirectory = join(root, "outside");
  const targetFile = join(outputDirectory, "llms.txt");
  const sentinel = join(outsideDirectory, "llms.txt");
  await mkdir(outputDirectory);
  await mkdir(outsideDirectory);
  await writeFile(targetFile, "old", "utf8");
  await writeFile(sentinel, "outside", "utf8");

  let injected = false;
  const racingFileSystem = {
    async rename(source, target) {
      if (!injected && target === targetFile) {
        injected = true;
        await rename(outputDirectory, preservedOutput);
        await symlink(
          outsideDirectory,
          outputDirectory,
          process.platform === "win32" ? "junction" : "dir",
        );
      }
      return rename(source, target);
    },
  };

  await assert.rejects(
    atomicWriteSafeSiteFile({
      siteDirectory,
      targetFile,
      content: "replacement",
      label: "llms parent race regression",
      fileSystem: racingFileSystem,
    }),
  );

  assert.equal(await readFile(sentinel, "utf8"), "outside");
  assert.equal(await readFile(join(preservedOutput, "llms.txt"), "utf8"), "old");
});

test("directory staging failure preserves the old generated tree", async (t) => {
  const { siteDirectory } = await temporarySite(t);
  const contentDirectory = join(siteDirectory, "src", "content");
  const targetDirectory = join(contentDirectory, "docs");
  await mkdir(targetDirectory, { recursive: true });
  await writeFile(join(targetDirectory, "old.md"), "old", "utf8");

  await assert.rejects(
    replaceSafeSiteDirectory({
      siteDirectory,
      targetDirectory,
      label: "generated docs regression",
      populate: async (stagingDirectory) => {
        await writeFile(join(stagingDirectory, "partial.md"), "partial", "utf8");
        throw new Error("catalog rendering failed");
      },
    }),
    /catalog rendering failed/u,
  );

  assert.equal(await readFile(join(targetDirectory, "old.md"), "utf8"), "old");
  assert.deepEqual(await readdir(contentDirectory), ["docs"]);
});

test("directory replacement detects a last-moment junction swap without deleting either tree", async (t) => {
  const { root, siteDirectory } = await temporarySite(t);
  const contentDirectory = join(siteDirectory, "src", "content");
  const targetDirectory = join(contentDirectory, "docs");
  const preservedDirectory = join(contentDirectory, "preserved-old-docs");
  const outsideDirectory = join(root, "outside");
  await mkdir(targetDirectory, { recursive: true });
  await mkdir(outsideDirectory);
  await writeFile(join(targetDirectory, "old.md"), "old", "utf8");
  await writeFile(join(outsideDirectory, "sentinel.txt"), "outside", "utf8");

  let injected = false;
  const racingFileSystem = {
    async rename(source, target) {
      if (!injected && source === targetDirectory) {
        injected = true;
        await rename(targetDirectory, preservedDirectory);
        await symlink(
          outsideDirectory,
          targetDirectory,
          process.platform === "win32" ? "junction" : "dir",
        );
      }
      return rename(source, target);
    },
  };

  await assert.rejects(
    replaceSafeSiteDirectory({
      siteDirectory,
      targetDirectory,
      label: "generated docs race regression",
      fileSystem: racingFileSystem,
      populate: async (stagingDirectory) => {
        await writeFile(join(stagingDirectory, "new.md"), "new", "utf8");
      },
    }),
    /(?:changed during atomic replacement|symlink, junction, or reparse point)/u,
  );

  assert.equal(await readFile(join(outsideDirectory, "sentinel.txt"), "utf8"), "outside");
  assert.equal(await readFile(join(preservedDirectory, "old.md"), "utf8"), "old");
  const leftovers = await readdir(contentDirectory);
  assert.ok(leftovers.includes(basename(targetDirectory)) === false);
  assert.ok(leftovers.includes(basename(preservedDirectory)));
  assert.equal(leftovers.some((name) => name.includes("stage")), false);
});
