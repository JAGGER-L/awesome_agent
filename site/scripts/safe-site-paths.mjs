import { constants } from "node:fs";
import {
  lstat,
  mkdir,
  open,
  readdir,
  realpath,
  rename,
  rmdir,
  unlink,
} from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";

export class SafeSitePathError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "SafeSitePathError";
  }
}

const defaultFileSystem = {
  lstat,
  mkdir,
  open,
  readdir,
  realpath,
  rename,
  rmdir,
  unlink,
};

function withDefaultFileSystem(fileSystem) {
  return { ...defaultFileSystem, ...fileSystem };
}

function isContained(root, target) {
  const relativeTarget = relative(root, target);
  return (
    relativeTarget === "" ||
    (relativeTarget !== ".." &&
      !relativeTarget.startsWith(`..${sep}`) &&
      !isAbsolute(relativeTarget))
  );
}

function isLinkOrReparsePoint(metadata) {
  return (
    metadata.isSymbolicLink?.() === true ||
    metadata.isReparsePoint?.() === true ||
    metadata.reparsePoint === true
  );
}

function assertOrdinaryNode(metadata, expectedKind, path, label) {
  if (isLinkOrReparsePoint(metadata)) {
    throw new SafeSitePathError(
      `${label} crosses a symlink, junction, or reparse point: ${path}`,
    );
  }

  const matches =
    expectedKind === "directory"
      ? metadata.isDirectory?.() === true
      : metadata.isFile?.() === true;
  if (!matches) {
    throw new SafeSitePathError(
      `${label} requires an ordinary ${expectedKind}: ${path}`,
    );
  }
}

function nodeIdentity(metadata, path, label) {
  if (metadata.dev === undefined || metadata.ino === undefined) {
    throw new SafeSitePathError(`${label} cannot bind filesystem identity for ${path}.`);
  }
  return Object.freeze({ dev: String(metadata.dev), ino: String(metadata.ino) });
}

function identitiesMatch(left, right) {
  return left?.dev === right?.dev && left?.ino === right?.ino;
}

function assertSameIdentity(actual, expected, path, label) {
  if (!identitiesMatch(actual, expected)) {
    throw new SafeSitePathError(`${label} changed during atomic replacement: ${path}`);
  }
}

async function requiredLstat(path, label, fileSystem) {
  try {
    return await fileSystem.lstat(path);
  } catch (error) {
    throw new SafeSitePathError(`${label} cannot inspect ${path}.`, { cause: error });
  }
}

async function requiredRealpath(path, label, fileSystem) {
  try {
    return await fileSystem.realpath(path);
  } catch (error) {
    throw new SafeSitePathError(`${label} cannot resolve ${path}.`, { cause: error });
  }
}

async function optionalLstat(path, label, fileSystem) {
  try {
    return await fileSystem.lstat(path);
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw new SafeSitePathError(`${label} cannot inspect ${path}.`, { cause: error });
  }
}

async function assertSafeSitePath({
  siteDirectory,
  targetPath,
  targetKind,
  allowMissingTarget,
  label,
  fileSystem = defaultFileSystem,
}) {
  fileSystem = withDefaultFileSystem(fileSystem);
  if (targetKind !== "directory" && targetKind !== "file") {
    throw new SafeSitePathError(`${label} has an unsupported target kind: ${targetKind}`);
  }

  const siteRoot = resolve(siteDirectory);
  const target = resolve(targetPath);
  const relativeTarget = relative(siteRoot, target);
  if (
    relativeTarget === "" ||
    relativeTarget === ".." ||
    relativeTarget.startsWith(`..${sep}`) ||
    isAbsolute(relativeTarget)
  ) {
    throw new SafeSitePathError(`${label} must be a strict descendant of the site root.`);
  }

  const siteMetadata = await requiredLstat(siteRoot, label, fileSystem);
  assertOrdinaryNode(siteMetadata, "directory", siteRoot, label);
  const realSiteRoot = await requiredRealpath(siteRoot, label, fileSystem);

  const segments = relativeTarget.split(sep);
  let current = siteRoot;
  let realCurrent = realSiteRoot;
  for (let index = 0; index < segments.length; index += 1) {
    current = join(current, segments[index]);
    const metadata = await optionalLstat(current, label, fileSystem);
    if (metadata === null) {
      if (!allowMissingTarget) {
        throw new SafeSitePathError(`${label} does not exist: ${target}`);
      }
      const projectedTarget = resolve(realCurrent, ...segments.slice(index));
      if (!isContained(realSiteRoot, projectedTarget)) {
        throw new SafeSitePathError(`${label} escapes the real site root: ${target}`);
      }
      return Object.freeze({
        exists: false,
        realSiteRoot,
        target,
        projectedTarget,
        identity: null,
      });
    }

    const expectedKind =
      index === segments.length - 1 && targetKind === "file" ? "file" : "directory";
    assertOrdinaryNode(metadata, expectedKind, current, label);
    realCurrent = await requiredRealpath(current, label, fileSystem);
    if (!isContained(realSiteRoot, realCurrent)) {
      throw new SafeSitePathError(`${label} escapes the real site root: ${current}`);
    }
  }

  const finalMetadata = await requiredLstat(target, label, fileSystem);
  assertOrdinaryNode(finalMetadata, targetKind, target, label);
  const finalRealTarget = await requiredRealpath(target, label, fileSystem);
  if (!isContained(realSiteRoot, finalRealTarget)) {
    throw new SafeSitePathError(`${label} escapes the real site root: ${target}`);
  }

  return Object.freeze({
    exists: true,
    realSiteRoot,
    target,
    projectedTarget: finalRealTarget,
    identity: nodeIdentity(finalMetadata, target, label),
  });
}

export async function assertSafeSiteDirectory({
  siteDirectory,
  targetDirectory,
  allowMissingTarget = false,
  label = "Site directory mutation",
  fileSystem,
}) {
  return assertSafeSitePath({
    siteDirectory,
    targetPath: targetDirectory,
    targetKind: "directory",
    allowMissingTarget,
    label,
    fileSystem,
  });
}

export async function assertSafeSiteFile({
  siteDirectory,
  targetFile,
  allowMissingTarget = false,
  label = "Site file mutation",
  fileSystem,
}) {
  return assertSafeSitePath({
    siteDirectory,
    targetPath: targetFile,
    targetKind: "file",
    allowMissingTarget,
    label,
    fileSystem,
  });
}

async function safeUnlinkBoundFile(path, expectedIdentity, label, fileSystem) {
  const metadata = await optionalLstat(path, label, fileSystem);
  if (metadata === null) return;
  assertOrdinaryNode(metadata, "file", path, label);
  assertSameIdentity(nodeIdentity(metadata, path, label), expectedIdentity, path, label);
  await fileSystem.unlink(path);
}

async function removeBoundDirectory(path, expectedIdentity, label, fileSystem) {
  const rootMetadata = await requiredLstat(path, label, fileSystem);
  assertOrdinaryNode(rootMetadata, "directory", path, label);
  assertSameIdentity(
    nodeIdentity(rootMetadata, path, label),
    expectedIdentity,
    path,
    label,
  );

  const entries = await fileSystem.readdir(path, { withFileTypes: true });
  for (const entry of entries) {
    const child = join(path, entry.name);
    const metadata = await requiredLstat(child, label, fileSystem);
    if (isLinkOrReparsePoint(metadata)) {
      throw new SafeSitePathError(
        `${label} refuses to clean a symlink, junction, or reparse point: ${child}`,
      );
    }
    if (metadata.isDirectory?.() === true) {
      await removeBoundDirectory(
        child,
        nodeIdentity(metadata, child, label),
        label,
        fileSystem,
      );
      continue;
    }
    if (metadata.isFile?.() !== true) {
      throw new SafeSitePathError(`${label} refuses to clean a non-ordinary node: ${child}`);
    }
    await safeUnlinkBoundFile(
      child,
      nodeIdentity(metadata, child, label),
      label,
      fileSystem,
    );
  }

  const finalMetadata = await requiredLstat(path, label, fileSystem);
  assertOrdinaryNode(finalMetadata, "directory", path, label);
  assertSameIdentity(
    nodeIdentity(finalMetadata, path, label),
    expectedIdentity,
    path,
    label,
  );
  await fileSystem.rmdir(path);
}

async function validateTree(path, expectedIdentity, label, fileSystem) {
  const metadata = await requiredLstat(path, label, fileSystem);
  assertOrdinaryNode(metadata, "directory", path, label);
  assertSameIdentity(nodeIdentity(metadata, path, label), expectedIdentity, path, label);
  for (const entry of await fileSystem.readdir(path, { withFileTypes: true })) {
    const child = join(path, entry.name);
    const childMetadata = await requiredLstat(child, label, fileSystem);
    if (isLinkOrReparsePoint(childMetadata)) {
      throw new SafeSitePathError(
        `${label} contains a symlink, junction, or reparse point: ${child}`,
      );
    }
    if (childMetadata.isDirectory?.() === true) {
      await validateTree(
        child,
        nodeIdentity(childMetadata, child, label),
        label,
        fileSystem,
      );
    } else if (childMetadata.isFile?.() !== true) {
      throw new SafeSitePathError(`${label} contains a non-ordinary node: ${child}`);
    }
  }
}

function uniqueSibling(path, purpose) {
  return join(dirname(path), `.${basename(path)}.awesome-${purpose}-${randomUUID()}`);
}

export async function ensureSafeSiteDirectory({
  siteDirectory,
  targetDirectory,
  label = "Site directory creation",
  fileSystem,
}) {
  const fs = withDefaultFileSystem(fileSystem);
  const siteRoot = resolve(siteDirectory);
  const target = resolve(targetDirectory);
  const relativeTarget = relative(siteRoot, target);
  if (
    relativeTarget === "" ||
    relativeTarget === ".." ||
    relativeTarget.startsWith(`..${sep}`) ||
    isAbsolute(relativeTarget)
  ) {
    throw new SafeSitePathError(`${label} must be a strict descendant of the site root.`);
  }

  const siteMetadata = await requiredLstat(siteRoot, label, fs);
  assertOrdinaryNode(siteMetadata, "directory", siteRoot, label);
  await requiredRealpath(siteRoot, label, fs);

  let current = siteRoot;
  for (const segment of relativeTarget.split(sep)) {
    current = join(current, segment);
    const before = await optionalLstat(current, label, fs);
    if (before === null) {
      try {
        await fs.mkdir(current);
      } catch (error) {
        if (error?.code !== "EEXIST") {
          throw new SafeSitePathError(`${label} cannot create ${current}.`, {
            cause: error,
          });
        }
      }
    }
    await assertSafeSiteDirectory({
      siteDirectory: siteRoot,
      targetDirectory: current,
      label,
      fileSystem: fs,
    });
  }
}

export async function atomicWriteSafeSiteFile({
  siteDirectory,
  targetFile,
  content,
  encoding = "utf8",
  label = "Atomic site file replacement",
  fileSystem,
}) {
  const fs = withDefaultFileSystem(fileSystem);
  const target = resolve(targetFile);
  const parent = dirname(target);
  const parentBefore = await assertSafeSiteDirectory({
    siteDirectory,
    targetDirectory: parent,
    label,
    fileSystem: fs,
  });
  const targetBefore = await assertSafeSiteFile({
    siteDirectory,
    targetFile: target,
    allowMissingTarget: true,
    label,
    fileSystem: fs,
  });
  const temporary = uniqueSibling(target, "tmp");
  let handle = null;
  let temporaryIdentity = null;
  let committed = false;

  try {
    const flags =
      constants.O_CREAT |
      constants.O_EXCL |
      constants.O_WRONLY |
      (constants.O_NOFOLLOW ?? 0);
    handle = await fs.open(temporary, flags, 0o600);
    const handleMetadata = await handle.stat();
    temporaryIdentity = nodeIdentity(handleMetadata, temporary, label);
    await handle.writeFile(content, { encoding });
    await handle.sync();
    await handle.close();
    handle = null;

    const temporaryPath = await assertSafeSiteFile({
      siteDirectory,
      targetFile: temporary,
      label,
      fileSystem: fs,
    });
    assertSameIdentity(temporaryPath.identity, temporaryIdentity, temporary, label);
    const parentNow = await assertSafeSiteDirectory({
      siteDirectory,
      targetDirectory: parent,
      label,
      fileSystem: fs,
    });
    assertSameIdentity(parentNow.identity, parentBefore.identity, parent, label);
    const targetNow = await assertSafeSiteFile({
      siteDirectory,
      targetFile: target,
      allowMissingTarget: true,
      label,
      fileSystem: fs,
    });
    if (targetBefore.exists !== targetNow.exists) {
      throw new SafeSitePathError(`${label} changed during atomic replacement: ${target}`);
    }
    if (targetBefore.exists) {
      assertSameIdentity(targetNow.identity, targetBefore.identity, target, label);
    }

    await fs.rename(temporary, target);
    const installed = await assertSafeSiteFile({
      siteDirectory,
      targetFile: target,
      label,
      fileSystem: fs,
    });
    assertSameIdentity(installed.identity, temporaryIdentity, target, label);
    committed = true;
  } finally {
    if (handle !== null) await handle.close().catch(() => {});
    if (!committed && temporaryIdentity !== null) {
      await safeUnlinkBoundFile(temporary, temporaryIdentity, label, fs).catch(() => {});
    }
  }
}

export async function replaceSafeSiteDirectory({
  siteDirectory,
  targetDirectory,
  populate,
  label = "Atomic site directory replacement",
  fileSystem,
}) {
  const fs = withDefaultFileSystem(fileSystem);
  const target = resolve(targetDirectory);
  const parent = dirname(target);
  const parentBefore = await assertSafeSiteDirectory({
    siteDirectory,
    targetDirectory: parent,
    label,
    fileSystem: fs,
  });
  const targetBefore = await assertSafeSiteDirectory({
    siteDirectory,
    targetDirectory: target,
    allowMissingTarget: true,
    label,
    fileSystem: fs,
  });
  const staging = uniqueSibling(target, "stage");
  const backup = uniqueSibling(target, "backup");
  let stagingIdentity = null;
  let backupIdentity = null;
  let installed = false;

  try {
    await fs.mkdir(staging);
    const stagingPath = await assertSafeSiteDirectory({
      siteDirectory,
      targetDirectory: staging,
      label,
      fileSystem: fs,
    });
    stagingIdentity = stagingPath.identity;
    await populate(staging);
    await validateTree(staging, stagingIdentity, label, fs);

    const parentNow = await assertSafeSiteDirectory({
      siteDirectory,
      targetDirectory: parent,
      label,
      fileSystem: fs,
    });
    assertSameIdentity(parentNow.identity, parentBefore.identity, parent, label);
    const targetNow = await assertSafeSiteDirectory({
      siteDirectory,
      targetDirectory: target,
      allowMissingTarget: true,
      label,
      fileSystem: fs,
    });
    if (targetBefore.exists !== targetNow.exists) {
      throw new SafeSitePathError(`${label} changed during atomic replacement: ${target}`);
    }
    if (targetBefore.exists) {
      assertSameIdentity(targetNow.identity, targetBefore.identity, target, label);
      await fs.rename(target, backup);
      const moved = await assertSafeSiteDirectory({
        siteDirectory,
        targetDirectory: backup,
        label,
        fileSystem: fs,
      });
      assertSameIdentity(moved.identity, targetBefore.identity, target, label);
      backupIdentity = moved.identity;
    }

    await fs.rename(staging, target);
    const replacement = await assertSafeSiteDirectory({
      siteDirectory,
      targetDirectory: target,
      label,
      fileSystem: fs,
    });
    assertSameIdentity(replacement.identity, stagingIdentity, target, label);
    installed = true;

    if (backupIdentity !== null) {
      await removeBoundDirectory(backup, backupIdentity, label, fs);
      backupIdentity = null;
    }
  } catch (error) {
    if (!installed && backupIdentity !== null) {
      try {
        const currentTarget = await optionalLstat(target, label, fs);
        if (currentTarget === null) {
          const currentBackup = await assertSafeSiteDirectory({
            siteDirectory,
            targetDirectory: backup,
            label,
            fileSystem: fs,
          });
          assertSameIdentity(currentBackup.identity, backupIdentity, backup, label);
          await fs.rename(backup, target);
          const restored = await assertSafeSiteDirectory({
            siteDirectory,
            targetDirectory: target,
            label,
            fileSystem: fs,
          });
          assertSameIdentity(restored.identity, backupIdentity, target, label);
          backupIdentity = null;
        }
      } catch {
        // The verified backup is retained for manual recovery if rollback races.
      }
    }
    throw error;
  } finally {
    if (!installed && stagingIdentity !== null) {
      await removeBoundDirectory(staging, stagingIdentity, label, fs).catch(() => {});
    }
  }
}
