import assert from "node:assert/strict";
import { join, relative, resolve, sep } from "node:path";
import test from "node:test";

import {
  createDocumentationInputReader,
  DocumentationInputSafetyError,
} from "./documentation-input-safety.mjs";

function metadata(kind, identity, content = Buffer.alloc(0)) {
  return {
    dev: 1n,
    ino: BigInt(identity),
    size: BigInt(content.length),
    mtimeNs: 10n,
    ctimeNs: 10n,
    isDirectory: () => kind === "directory" || kind === "reparse",
    isFile: () => kind === "file",
    isReparsePoint: () => kind === "reparse",
    isSymbolicLink: () => kind === "link",
  };
}

function fakeFileSystem(
  records,
  { lstatSequences = new Map(), openMetadata = new Map() } = {},
) {
  const nodes = new Map(
    records.map(([path, kind, identity, value = "", realPath]) => {
      const content = Buffer.isBuffer(value) ? value : Buffer.from(value);
      return [
        resolve(path),
        {
          content,
          metadata: metadata(kind, identity, content),
          realPath: resolve(realPath ?? path),
        },
      ];
    }),
  );
  const sequences = new Map(
    [...lstatSequences].map(([path, values]) => [
      resolve(path),
      values.map(([kind, identity, value = ""]) => {
        const content = Buffer.isBuffer(value) ? value : Buffer.from(value);
        return metadata(kind, identity, content);
      }),
    ]),
  );
  const opened = new Map(
    [...openMetadata].map(([path, [kind, identity, value = ""]]) => {
      const content = Buffer.isBuffer(value) ? value : Buffer.from(value);
      return [
        resolve(path),
        { content, metadata: metadata(kind, identity, content) },
      ];
    }),
  );
  const openFlags = [];

  function requiredNode(path) {
    const key = resolve(path);
    const node = nodes.get(key);
    if (!node) {
      throw Object.assign(new Error(`Missing fixture path: ${key}`), {
        code: "ENOENT",
      });
    }
    return { key, node };
  }

  return {
    noFollowFlag: 0x20_000,
    readOnlyFlag: 0,
    openFlags,
    async lstat(path) {
      const { key, node } = requiredNode(path);
      const sequence = sequences.get(key);
      if (sequence?.length > 0) return sequence.shift();
      return node.metadata;
    },
    async realpath(path) {
      return requiredNode(path).node.realPath;
    },
    async readdir(path) {
      const { key, node } = requiredNode(path);
      if (!node.metadata.isDirectory()) {
        throw Object.assign(new Error("Not a directory"), { code: "ENOTDIR" });
      }
      const names = new Set();
      for (const candidate of nodes.keys()) {
        const child = relative(key, candidate);
        if (
          child !== "" &&
          child !== ".." &&
          !child.startsWith(`..${sep}`) &&
          !child.includes(sep)
        ) {
          names.add(child);
        }
      }
      return [...names].map((name) => ({ name }));
    },
    async open(path, flags) {
      const { key, node } = requiredNode(path);
      openFlags.push(flags);
      const snapshot = opened.get(key) ?? node;
      let closed = false;
      return {
        async stat() {
          assert.equal(closed, false);
          return snapshot.metadata;
        },
        async read(buffer, offset, length, position) {
          assert.equal(closed, false);
          const bytesRead = Math.min(
            length,
            Math.max(0, snapshot.content.length - position),
          );
          snapshot.content.copy(
            buffer,
            offset,
            position,
            position + bytesRead,
          );
          return { buffer, bytesRead };
        },
        async close() {
          closed = true;
        },
      };
    },
  };
}

const repository = resolve("virtual", "repository");
const docs = join(repository, "docs");
const guide = join(docs, "guide.md");

function ordinaryTree(content = "# Guide\n") {
  return [
    [repository, "directory", 1],
    [docs, "directory", 2],
    [guide, "file", 3, content],
  ];
}

test("discovers and reads only bounded UTF-8 files with no-follow when supported", async () => {
  const fileSystem = fakeFileSystem(ordinaryTree("# Guide\nSafe input.\n"));
  const reader = await createDocumentationInputReader({
    repositoryRoot: repository,
    fileSystem,
  });

  assert.deepEqual(await reader.listMarkdownSources(), ["docs/guide.md"]);
  const record = await reader.readUtf8Source("docs/guide.md");
  assert.equal(record.content, "# Guide\nSafe input.\n");
  assert.equal(fileSystem.openFlags.length, 1);
  assert.equal(fileSystem.openFlags[0] & fileSystem.noFollowFlag, 0x20_000);
});

test("rejects a linked or reparse docs root and Markdown file", async () => {
  for (const unsafeKind of ["link", "reparse"]) {
    const unsafeRoot = fakeFileSystem([
      [repository, "directory", 1],
      [docs, unsafeKind, 2],
    ]);
    const rootReader = await createDocumentationInputReader({
      repositoryRoot: repository,
      fileSystem: unsafeRoot,
    });
    await assert.rejects(
      rootReader.listMarkdownSources(),
      /symlink, junction, or reparse point/u,
    );

    const unsafeFile = fakeFileSystem([
      [repository, "directory", 1],
      [docs, "directory", 2],
      [guide, unsafeKind, 3],
    ]);
    const fileReader = await createDocumentationInputReader({
      repositoryRoot: repository,
      fileSystem: unsafeFile,
    });
    await assert.rejects(
      fileReader.listMarkdownSources(),
      /symlink, junction, or reparse point/u,
    );
  }
});

test("rejects realpath escape and nonordinary source nodes", async () => {
  const escaped = fakeFileSystem([
    [repository, "directory", 1],
    [docs, "directory", 2, "", resolve("virtual", "outside")],
  ]);
  const escapedReader = await createDocumentationInputReader({
    repositoryRoot: repository,
    fileSystem: escaped,
  });
  await assert.rejects(
    escapedReader.listMarkdownSources(),
    /escapes the real repository root/u,
  );

  const nonordinary = fakeFileSystem([
    [repository, "directory", 1],
    [docs, "directory", 2],
    [guide, "socket", 3],
  ]);
  const nonordinaryReader = await createDocumentationInputReader({
    repositoryRoot: repository,
    fileSystem: nonordinary,
  });
  await assert.rejects(
    nonordinaryReader.listMarkdownSources(),
    /requires ordinary files and directories/u,
  );
});

test("rejects a docs directory replaced while it is scanned", async () => {
  const fileSystem = fakeFileSystem(ordinaryTree(), {
    lstatSequences: new Map([
      [
        docs,
        [
          ["directory", 2],
          ["directory", 20],
        ],
      ],
    ]),
  });
  const reader = await createDocumentationInputReader({
    repositoryRoot: repository,
    fileSystem,
  });
  await assert.rejects(
    reader.listMarkdownSources(),
    /changed filesystem identity during inspection/u,
  );
});

test("rejects a source replaced between lstat and open", async () => {
  const fileSystem = fakeFileSystem(ordinaryTree(), {
    openMetadata: new Map([[guide, ["file", 30, "# Replaced\n"]]]),
  });
  const reader = await createDocumentationInputReader({
    repositoryRoot: repository,
    fileSystem,
  });
  await assert.rejects(
    reader.readUtf8Source("docs/guide.md"),
    /changed while it was being read/u,
  );
});

test("enforces the byte bound and fatal UTF-8 decoding", async () => {
  const oversized = fakeFileSystem(ordinaryTree("12345"));
  const boundedReader = await createDocumentationInputReader({
    repositoryRoot: repository,
    fileSystem: oversized,
    maximumBytes: 4,
  });
  await assert.rejects(
    boundedReader.readUtf8Source("docs/guide.md"),
    /exceeds 4 bytes/u,
  );

  const invalidUtf8 = fakeFileSystem(ordinaryTree(Buffer.from([0xc3, 0x28])));
  const utf8Reader = await createDocumentationInputReader({
    repositoryRoot: repository,
    fileSystem: invalidUtf8,
  });
  await assert.rejects(
    utf8Reader.readUtf8Source("docs/guide.md"),
    /is not valid UTF-8/u,
  );
});

test("rejects repository-relative paths and absolute lock paths that escape", async () => {
  const fileSystem = fakeFileSystem(ordinaryTree());
  const reader = await createDocumentationInputReader({
    repositoryRoot: repository,
    fileSystem,
  });
  await assert.rejects(
    reader.readUtf8Source("../outside.md"),
    DocumentationInputSafetyError,
  );
  await assert.rejects(
    reader.readUtf8Path(resolve(repository, "..", "translation-lock.json")),
    /strict descendant of the repository root/u,
  );
});
