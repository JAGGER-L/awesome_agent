import { constants } from "node:fs";
import { lstat, open, readdir, realpath } from "node:fs/promises";
import { isAbsolute, join, relative, resolve, sep } from "node:path";

export const MAX_DOCUMENTATION_SOURCE_BYTES = 1024 * 1024;

export class DocumentationInputSafetyError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "DocumentationInputSafetyError";
  }
}

const defaultFileSystem = {
  lstat,
  open,
  readdir,
  realpath,
  readOnlyFlag: constants.O_RDONLY,
  noFollowFlag: constants.O_NOFOLLOW,
};

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
    throw new DocumentationInputSafetyError(
      `${label} crosses a symlink, junction, or reparse point: ${path}`,
    );
  }
  const matches =
    expectedKind === "directory"
      ? metadata.isDirectory?.() === true
      : metadata.isFile?.() === true;
  if (!matches) {
    throw new DocumentationInputSafetyError(
      `${label} requires an ordinary ${expectedKind}: ${path}`,
    );
  }
}

function metadataValue(metadata, field) {
  const value = metadata[field];
  return value === undefined || value === null ? null : String(value);
}

function nodeIdentity(metadata, path, label) {
  const dev = metadataValue(metadata, "dev");
  const ino = metadataValue(metadata, "ino");
  if (dev === null || ino === null) {
    throw new DocumentationInputSafetyError(
      `${label} cannot establish a stable filesystem identity: ${path}`,
    );
  }
  return `${dev}:${ino}`;
}

function stableFileSnapshot(metadata, path, label) {
  const size = metadataValue(metadata, "size");
  const modified =
    metadataValue(metadata, "mtimeNs") ?? metadataValue(metadata, "mtimeMs");
  const changed =
    metadataValue(metadata, "ctimeNs") ?? metadataValue(metadata, "ctimeMs");
  if (size === null || modified === null || changed === null) {
    throw new DocumentationInputSafetyError(
      `${label} cannot establish stable file metadata: ${path}`,
    );
  }
  return `${nodeIdentity(metadata, path, label)}:${size}:${modified}:${changed}`;
}

function assertSameIdentity(before, after, path, label) {
  if (
    nodeIdentity(before, path, label) !== nodeIdentity(after, path, label)
  ) {
    throw new DocumentationInputSafetyError(
      `${label} changed filesystem identity during inspection: ${path}`,
    );
  }
}

function assertSameFileSnapshot(before, after, path, label) {
  if (
    stableFileSnapshot(before, path, label) !==
    stableFileSnapshot(after, path, label)
  ) {
    throw new DocumentationInputSafetyError(
      `${label} changed while it was being read: ${path}`,
    );
  }
}

async function requiredLstat(path, label, fileSystem) {
  try {
    return await fileSystem.lstat(path, { bigint: true });
  } catch (error) {
    throw new DocumentationInputSafetyError(
      `${label} cannot inspect ${path}: ${error.code ?? error.message}`,
      { cause: error },
    );
  }
}

async function requiredRealpath(path, label, fileSystem) {
  try {
    return await fileSystem.realpath(path);
  } catch (error) {
    throw new DocumentationInputSafetyError(
      `${label} cannot resolve ${path}: ${error.code ?? error.message}`,
      { cause: error },
    );
  }
}

function containedTarget(repositoryRoot, targetPath, label) {
  const target = resolve(targetPath);
  if (target === repositoryRoot || !isContained(repositoryRoot, target)) {
    throw new DocumentationInputSafetyError(
      `${label} must be a strict descendant of the repository root: ${target}`,
    );
  }
  return target;
}

async function assertRepositoryStable(context, label) {
  const current = await requiredLstat(
    context.repositoryRoot,
    label,
    context.fileSystem,
  );
  assertOrdinaryNode(
    current,
    "directory",
    context.repositoryRoot,
    label,
  );
  assertSameIdentity(
    context.repositoryMetadata,
    current,
    context.repositoryRoot,
    label,
  );
  const currentRealRoot = await requiredRealpath(
    context.repositoryRoot,
    label,
    context.fileSystem,
  );
  if (
    !isContained(context.realRepositoryRoot, currentRealRoot) ||
    !isContained(currentRealRoot, context.realRepositoryRoot)
  ) {
    throw new DocumentationInputSafetyError(
      `${label} repository root changed during inspection: ${context.repositoryRoot}`,
    );
  }
}

async function inspectContainedPath(context, targetPath, expectedKind, label) {
  const target = containedTarget(context.repositoryRoot, targetPath, label);
  await assertRepositoryStable(context, label);
  const relativeTarget = relative(context.repositoryRoot, target);
  const segments = relativeTarget.split(sep);
  let current = context.repositoryRoot;
  let metadata = context.repositoryMetadata;

  for (let index = 0; index < segments.length; index += 1) {
    current = join(current, segments[index]);
    metadata = await requiredLstat(current, label, context.fileSystem);
    const kind =
      index === segments.length - 1 ? expectedKind : "directory";
    assertOrdinaryNode(metadata, kind, current, label);
    const realCurrent = await requiredRealpath(
      current,
      label,
      context.fileSystem,
    );
    if (!isContained(context.realRepositoryRoot, realCurrent)) {
      throw new DocumentationInputSafetyError(
        `${label} escapes the real repository root: ${current}`,
      );
    }
  }

  return { metadata, target };
}

async function assertDirectorySnapshot(context, path, snapshot, label) {
  const current = await inspectContainedPath(context, path, "directory", label);
  assertSameIdentity(snapshot, current.metadata, path, label);
}

async function listMarkdownSources(context, docsDirectory) {
  const sources = [];

  async function walk(directory) {
    const label = "Documentation source discovery";
    const inspected = await inspectContainedPath(
      context,
      directory,
      "directory",
      label,
    );
    let entries;
    try {
      entries = await context.fileSystem.readdir(directory, {
        withFileTypes: true,
      });
    } catch (error) {
      throw new DocumentationInputSafetyError(
        `${label} cannot scan ${directory}: ${error.code ?? error.message}`,
        { cause: error },
      );
    }
    await assertDirectorySnapshot(
      context,
      directory,
      inspected.metadata,
      label,
    );

    for (const entry of [...entries].sort((left, right) =>
      left.name < right.name ? -1 : left.name > right.name ? 1 : 0,
    )) {
      if (
        entry.name === "" ||
        entry.name === "." ||
        entry.name === ".." ||
        entry.name.includes("/") ||
        entry.name.includes("\\")
      ) {
        throw new DocumentationInputSafetyError(
          `${label} returned an invalid directory entry: ${entry.name}`,
        );
      }
      await assertDirectorySnapshot(
        context,
        directory,
        inspected.metadata,
        label,
      );
      const child = join(directory, entry.name);
      const childMetadata = await requiredLstat(
        child,
        label,
        context.fileSystem,
      );
      if (isLinkOrReparsePoint(childMetadata)) {
        throw new DocumentationInputSafetyError(
          `${label} crosses a symlink, junction, or reparse point: ${child}`,
        );
      }
      if (childMetadata.isDirectory?.() === true) {
        await walk(child);
      } else if (childMetadata.isFile?.() === true) {
        const realChild = await requiredRealpath(
          child,
          label,
          context.fileSystem,
        );
        if (!isContained(context.realRepositoryRoot, realChild)) {
          throw new DocumentationInputSafetyError(
            `${label} escapes the real repository root: ${child}`,
          );
        }
        if (entry.name.endsWith(".md")) {
          sources.push(
            relative(context.repositoryRoot, child).replace(/\\/gu, "/"),
          );
        }
      } else {
        throw new DocumentationInputSafetyError(
          `${label} requires ordinary files and directories: ${child}`,
        );
      }
    }
    await assertDirectorySnapshot(
      context,
      directory,
      inspected.metadata,
      label,
    );
  }

  await walk(docsDirectory);
  return sources;
}

async function readBoundedFile(handle, maximumBytes, path, label) {
  const chunks = [];
  let total = 0;
  let position = 0;
  while (total <= maximumBytes) {
    const remaining = maximumBytes + 1 - total;
    if (remaining <= 0) break;
    const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, remaining));
    const { bytesRead } = await handle.read(
      buffer,
      0,
      buffer.length,
      position,
    );
    if (bytesRead === 0) break;
    chunks.push(buffer.subarray(0, bytesRead));
    total += bytesRead;
    position += bytesRead;
  }
  if (total > maximumBytes) {
    throw new DocumentationInputSafetyError(
      `${label} exceeds ${maximumBytes} bytes: ${path}`,
    );
  }
  return Buffer.concat(chunks, total);
}

async function readUtf8File(context, targetPath, label, maximumBytes) {
  const inspected = await inspectContainedPath(
    context,
    targetPath,
    "file",
    label,
  );
  const noFollowFlag = Number.isInteger(context.fileSystem.noFollowFlag)
    ? context.fileSystem.noFollowFlag
    : 0;
  const readOnlyFlag = Number.isInteger(context.fileSystem.readOnlyFlag)
    ? context.fileSystem.readOnlyFlag
    : constants.O_RDONLY;
  let handle;
  try {
    handle = await context.fileSystem.open(
      inspected.target,
      readOnlyFlag | noFollowFlag,
    );
  } catch (error) {
    throw new DocumentationInputSafetyError(
      `${label} cannot open ${inspected.target}: ${error.code ?? error.message}`,
      { cause: error },
    );
  }

  try {
    const openedMetadata = await handle.stat({ bigint: true });
    assertOrdinaryNode(openedMetadata, "file", inspected.target, label);
    assertSameFileSnapshot(
      inspected.metadata,
      openedMetadata,
      inspected.target,
      label,
    );
    const afterOpen = await inspectContainedPath(
      context,
      inspected.target,
      "file",
      label,
    );
    assertSameFileSnapshot(
      openedMetadata,
      afterOpen.metadata,
      inspected.target,
      label,
    );
    if (Number(openedMetadata.size) > maximumBytes) {
      throw new DocumentationInputSafetyError(
        `${label} exceeds ${maximumBytes} bytes: ${inspected.target}`,
      );
    }

    const bytes = await readBoundedFile(
      handle,
      maximumBytes,
      inspected.target,
      label,
    );
    const finalMetadata = await handle.stat({ bigint: true });
    assertSameFileSnapshot(
      openedMetadata,
      finalMetadata,
      inspected.target,
      label,
    );
    const finalPath = await inspectContainedPath(
      context,
      inspected.target,
      "file",
      label,
    );
    assertSameFileSnapshot(
      openedMetadata,
      finalPath.metadata,
      inspected.target,
      label,
    );

    let content;
    try {
      content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      throw new DocumentationInputSafetyError(
        `${label} is not valid UTF-8: ${inspected.target}`,
      );
    }
    if (content.includes("\u0000")) {
      throw new DocumentationInputSafetyError(
        `${label} contains a NUL byte: ${inspected.target}`,
      );
    }
    return { absolutePath: inspected.target, content };
  } finally {
    await handle.close();
  }
}

export async function createDocumentationInputReader({
  repositoryRoot,
  fileSystem = defaultFileSystem,
  maximumBytes = MAX_DOCUMENTATION_SOURCE_BYTES,
}) {
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes <= 0) {
    throw new DocumentationInputSafetyError(
      "Documentation input byte limit must be a positive safe integer",
    );
  }
  const root = resolve(repositoryRoot);
  const label = "Documentation repository root";
  const repositoryMetadata = await requiredLstat(root, label, fileSystem);
  assertOrdinaryNode(repositoryMetadata, "directory", root, label);
  const realRepositoryRoot = await requiredRealpath(root, label, fileSystem);
  const context = {
    fileSystem,
    realRepositoryRoot,
    repositoryMetadata,
    repositoryRoot: root,
  };

  return Object.freeze({
    repositoryRoot: root,
    async listMarkdownSources(directory = join(root, "docs")) {
      return listMarkdownSources(context, directory);
    },
    async readUtf8Path(targetPath, readLabel = "Documentation source") {
      return readUtf8File(
        context,
        targetPath,
        readLabel,
        maximumBytes,
      );
    },
    async readUtf8Source(source, readLabel = "Documentation source") {
      if (
        typeof source !== "string" ||
        source === "" ||
        source.includes("\\") ||
        source.startsWith("/")
      ) {
        throw new DocumentationInputSafetyError(
          `${readLabel} has an invalid repository-relative path: ${source}`,
        );
      }
      return readUtf8File(
        context,
        resolve(root, ...source.split("/")),
        readLabel,
        maximumBytes,
      );
    },
    async assertRepositoryStable() {
      await assertRepositoryStable(context, label);
    },
  });
}
