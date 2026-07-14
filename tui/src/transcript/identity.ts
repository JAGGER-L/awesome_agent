import { randomUUID } from "node:crypto";

export function createClientMessageId(): string {
  return `client_${randomUUID().replaceAll("-", "")}`;
}

export function createCommandSubmissionId(): string {
  return `command_${randomUUID().replaceAll("-", "")}`;
}
