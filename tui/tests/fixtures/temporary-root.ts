import { mkdtemp, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

export async function createCanonicalTemporaryRoot(
  prefix: string,
): Promise<string> {
  const canonicalTemporaryDirectory = await realpath(tmpdir());
  return await mkdtemp(join(canonicalTemporaryDirectory, prefix));
}
